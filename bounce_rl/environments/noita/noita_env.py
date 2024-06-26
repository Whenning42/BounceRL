import atexit
import datetime
import logging
import pathlib
import pickle
import random
import subprocess
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import gym
import numpy as np
import simplejpeg

import bounce_rl.configs.app_configs as app_configs
from bounce_rl.core.harness import Harness
from bounce_rl.core.keyboard import keyboard
from bounce_rl.core.keyboard.keyboard import lib_mpx_input
from bounce_rl.core.time_control import time_writer
from bounce_rl.environments.noita import noita_info, noita_reward
from bounce_rl.utilities.paths import project_root
from bounce_rl.utilities.util import GrowingCircularFIFOArray, LinearInterpolator

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)s %(message)s")


@dataclass
class StepVal:
    pixels: np.ndarray
    reward: float
    terminated: bool
    truncated: bool
    info: dict
    ep_step: int
    env_step: int


class NoitaState(Enum):
    UNKNOWN = 0
    RUNNING = 1
    GAME_OVER = 2


def is_overworld(info: dict) -> bool:
    if int(info["y"]) < -80:
        return True
    return False


class TerminateOnOverworld:
    """Terminate the episode if the player is in the overworld."""

    def __call__(self, step: StepVal) -> StepVal:
        if is_overworld(step.info):
            step.terminated = True
        return step

    def reset(self):
        pass


class TerminateOnSparseReward:
    """Terminate the episode if the reward is zero for too many steps."""

    def __init__(
        self,
        history_len: Optional[LinearInterpolator] = None,
        max_size: Optional[int] = None,
        log: bool = False,
    ):
        self.termination_penalty = 10
        self.log = log

        if max_size is None:
            max_size = 5 * 60 * 4
        self.max_size = max_size
        if history_len is None:
            history_len = LinearInterpolator(
                x_0=0, x_1=1000000, y_0=1.5 * 60 * 4, y_1=5 * 60 * 4, extrapolate=False
            )
        self.history_len = history_len
        self.reset()

    def __call__(self, step: StepVal) -> StepVal:
        self.reward_history.push(
            step.reward, int(self.history_len.get_value(step.ep_step))
        )
        reward_history = self.reward_history.get_array()
        if np.sum(reward_history != 0) == 0:
            step.reward -= self.termination_penalty
            step.terminated = True
            if self.log:
                print(
                    f"Terminated episode due to sparse reward at step {step.ep_step}."
                )
        return step

    def reset(self):
        self.reward_history = GrowingCircularFIFOArray(max_size=self.max_size)
        # Push a non-zero reward to prevent early termination.
        self.reward_history.push(1, 1)


class NoitaEnv(gym.core.Env):
    singleton_init = False

    def __init__(
        self,
        seed: Optional[int] = None,
        out_dir: Optional[str] = None,
        env_conf: Optional[dict] = None,
        # Defaults to TerminateOnOverworld and TerminateOnSparseReward
        step_wrappers: List[Optional[Callable[[StepVal], StepVal]]] = None,
        skip_startup: bool = False,
        x_pos: int = 0,
        y_pos: int = 0,
        instance: int = 0,
        run_config: Optional[Dict[str, Any]] = None,
    ):
        # The caller must call pre_init for this environment, however,
        # we sometime do so out of process, so a simple global flag
        # check doesn't work.
        # if not self.singleton_init:
        #     raise RuntimeError(
        #         "NoitaEnv.pre_init must be called before any NoitaEnv instances are created."
        #     )

        if not seed:
            seed = random.getrandbits(32)
            print("Noita env using random seed: ", seed)
        self.seed = seed

        self.out_dir = out_dir
        self.run_config = NoitaEnv._default_run_config()
        if run_config is not None:
            self.run_config.update(run_config)
        if env_conf is None:
            env_conf = {}
        self.env_conf = env_conf
        self.x_pos = x_pos
        self.y_pos = y_pos
        self.instance = instance
        self.app_config = app_configs.LoadAppConfig(self.run_config["app"])
        self.environment = {
            "ENV_PREFIX": f"/tmp/env_dirs_{self.instance}",
        }
        self.noita_info = noita_info.NoitaInfo(pipe_dir=self.environment["ENV_PREFIX"])
        self.reward_callback = noita_reward.NoitaReward()

        if step_wrappers is None:
            step_wrappers = [TerminateOnOverworld(), TerminateOnSparseReward(log=True)]
        self.step_wrappers = step_wrappers

        self.ep_step = 0
        self.ep_num = 0
        self.env_step = 0

        self._reset_env(skip_startup=skip_startup)

    @staticmethod
    def _default_run_config() -> Dict[str, Any]:
        return {
            "app": "Noita",
            "x_res": 640,
            "y_res": 360,
            "scale": 1,
            "run_rate": 4,
            "pause_rate": 0.02,
            "step_duration": 0.166,
            "pixels_every_n_episodes": 1,
            # There are 9 input actions in the environment, so policies may do
            # 1/sqrt(9) feature scaling on actions. To compensate, we scale mouse
            # coordinates here.
            "scale_mouse_coords": 3,
            "use_x_proxy": True,
        }

    @staticmethod
    def _observation_space(
        run_config: Optional[Dict[str, Any]] = None
    ) -> gym.spaces.Box:
        if run_config is None:
            run_config = NoitaEnv._default_run_config()

        return gym.spaces.Box(
            low=0,
            high=255,
            shape=(run_config["y_res"], run_config["x_res"], 3),
            dtype=np.uint8,
        )

    @property
    def observation_space(self):
        return NoitaEnv._observation_space()

    @staticmethod
    def _input_space():
        return [
            (None, "W", "S"),
            (None, "A", "D"),
            (
                None,
                "F",
            ),
            (
                None,
                "E",
            ),
            # Noita blows themself up too often if you give them the bomb at the outset.
            # ("1", "2", "3", "4"),
            (None, "1", "3", "4"),
            (None, "5", "6", "7", "8"),
            (None, keyboard.MouseButton.LEFT, keyboard.MouseButton.RIGHT),
        ]

    @property
    def input_space(self):
        return NoitaEnv._input_space()

    @staticmethod
    def _action_space() -> gym.spaces.Tuple:
        discrete_lens = [len(x) for x in NoitaEnv._input_space()]
        return gym.spaces.Tuple(
            (
                gym.spaces.MultiDiscrete(discrete_lens),
                gym.spaces.Box(low=-1, high=1, shape=(2,)),
            )
        )

    @property
    def action_space(self):
        return NoitaEnv._action_space()

    @classmethod
    def pre_init(cls, num_envs: int = 1):
        """Should be called before any NoitaEnv instances are created.

        Sets up the MPX cursors"""

        if cls.singleton_init:
            raise RuntimeError("NoitaEnv.pre_init has already been called.")

        for i in range(num_envs):
            time_writer.SetSpeedup(1, str(i))

        lib_mpx, lib_mpx_ffi = lib_mpx_input.make_lib_mpx_input()
        display = lib_mpx.open_display(b":0")
        for i in range(num_envs):
            lib_mpx.make_cursor(display, lib_mpx_input.cursor_name(i).encode("utf-8"))

        def cleanup_cursors():
            for i in range(num_envs):
                lib_mpx.delete_cursor(
                    display, lib_mpx_input.cursor_name(i).encode("utf-8")
                )
            lib_mpx.close_display(display)

        atexit.register(cleanup_cursors)
        cls.singleton_init = True

    def _reset_env(self, skip_startup: bool = False):
        # Raises a runtime error if the environment fails to start.
        time_writer.SetSpeedup(1, str(self.instance))
        for i in range(3):
            did_reset = self._try_reset_env(skip_startup=skip_startup)
            if did_reset:
                return
            print("WARNING: Failed to reset NoitaEnv. Retrying...")
        raise RuntimeError("Failed to reset NoitaEnv.")

    def _try_reset_env(self, skip_startup: bool = False) -> bool:
        # Returns True if the reset was successful.
        self.ep_step = 0
        self.ep_num += 1
        self.step_dir = f"{self.out_dir}/steps/ep_{self.ep_num}"
        pathlib.Path(self.step_dir).mkdir(parents=True, exist_ok=True)

        for wrapper in self.step_wrappers:
            wrapper.reset()

        if hasattr(self, "harness"):
            # Release keys before we delete the old harness instance.
            # Important because the new harness won't know which keys
            # were held.
            self.harness.keyboard.set_held_keys(set())
            time.sleep(0.1)
            self.harness.cleanup()
            # os.system("killall noita.exe")
            time.sleep(1)

        self._set_up_magic_numbers(self.seed)
        self.harness = Harness(
            self.app_config,
            self.run_config,
            x_pos=self.x_pos,
            y_pos=self.y_pos,
            instance=self.instance,
            environment=self.environment,
        )
        self.state = NoitaState.UNKNOWN
        harness_init = self._wait_for_harness_init()
        if not harness_init:
            self.harness.cleanup()
            del self.harness
            return False
        self._env_init(skip_startup=skip_startup)
        return True

    def _wait_for_harness_init(self) -> bool:
        # Returns True if the harness was initialized.
        init_watch_dog = datetime.datetime.now()
        while not self.harness.ready:
            self.harness.tick()
            time.sleep(1)
            if (datetime.datetime.now() - init_watch_dog).total_seconds() > 45:
                print("Harness init timed out.")
                return False
        return True

    def _run_init_sequence(self):
        time.sleep(2.5)
        # Start the game
        menu_keys = (
            # Dismiss changelog
            "Return",
            # Start a new game
            "Down",
            "Return",
            "Return",
        )
        self.harness.keyboard.move_mouse(10, 10)
        self.harness.keyboard.key_sequence(menu_keys)

        time.sleep(8)
        """
        # Fly into the mines
        time.sleep(10)
        run_sequence = ((7.2, ("D",)), (1.0, ("W", "D")), (6.5, ("D",)))
        for t, keys in run_sequence + ((0, ()),):
            self.harness.keyboard.set_held_keys(keys)
            time.sleep(t)
        """

    def _env_init(self, skip_startup: bool):
        if not skip_startup:
            self._run_init_sequence()
        self.state = NoitaState.RUNNING

    # Stable baselines3 requires a seed method.
    def seed(self, seed):
        pass

    # SB3 expects `done` instead of `terminated` and `truncated`.
    def step(
        self, action: Tuple[Iterable, Iterable]
    ) -> Optional[Tuple[np.ndarray, float, bool, dict]]:
        """Returns None if the environment is unable to be stepped. In this case
        the environment should be reset.""" ""
        self.ep_step += 1
        self.env_step += 1

        # Convert actions to device inputs
        # My SB3 implementation flattens the space, here we split it back out again.
        orig_action = action
        if len(action) == 9:
            action = action[0:7], action[7:9]
        discrete_action, continuous_action = action
        discrete_action = [int(x) for x in discrete_action]

        held_mouse_buttons = set()
        held_keys = set()
        for i, s in zip(discrete_action, self.input_space):
            if s[i] is not None and not isinstance(s[i], keyboard.MouseButton):
                held_keys.add(s[i])
            if s[i] is not None and isinstance(s[i], keyboard.MouseButton):
                held_mouse_buttons.add(s[i])

        # Apply inputs
        self.harness.keyboard.set_held_keys(held_keys)
        self.harness.keyboard.set_held_mouse_buttons(held_mouse_buttons)
        continuous_action = [
            c * self.run_config["scale_mouse_coords"] for c in continuous_action
        ]
        # Convert mouse coordinates from [-1, 1] to [0, 1].
        continuous_action = [(c + 1) / 2 for c in continuous_action]
        mouse_pos = (
            continuous_action[0] * self.run_config["x_res"],
            continuous_action[1] * self.run_config["y_res"],
        )
        self.harness.keyboard.move_mouse(*mouse_pos)

        # Step the harness
        # TODO: Move time control into harness
        self.harness.tick()
        init_info = self.noita_info.current_info()
        retries = 20
        for i in range(retries):
            time_writer.SetSpeedup(self.run_config["run_rate"], str(self.instance))
            time.sleep(self.run_config["step_duration"] / self.run_config["run_rate"])
            time_writer.SetSpeedup(self.run_config["pause_rate"], str(self.instance))
            info = self.noita_info.on_tick()
            if info["tick"] != init_info["tick"]:
                break
            if i == retries - 1:
                logging.warning(
                    "NoitaEnv: Failed to step the environment on instance: %s.",
                    self.instance,
                )
                return None
        pixels = self.harness.get_screen()

        # Compute step values
        reward = self.reward_callback.update(info)
        terminated = not info["is_alive"]
        truncated = False
        step_val = StepVal(
            pixels, reward, terminated, truncated, info, self.ep_step, self.env_step
        )

        # Apply any step wrappers
        for wrapper in self.step_wrappers:
            step_val = wrapper(step_val)

        # Save step values minus pixels
        save_val = StepVal(
            None,
            step_val.reward,
            step_val.terminated,
            step_val.truncated,
            step_val.info,
            step_val.ep_step,
            step_val.env_step,
        )
        np.save(f"{self.step_dir}/step_{self.ep_step}.npy", save_val)

        # Save actions
        action_path = f"{self.step_dir}/action_{self.ep_step}.pkl"
        with open(action_path, "wb") as f:
            pickle.dump(orig_action, f)

        self._log_step(action, step_val)

        # return pixels, reward, terminated, truncated, info
        return (
            step_val.pixels,
            step_val.reward,
            step_val.terminated or step_val.truncated,
            step_val.info,
        )

    # SB3 doesn't handle info returned in reset method.
    # def reset(self, *, seed: Any = None, options: Any = None) -> Tuple[gym.core.ObsType, dict]:
    def reset(self, *, seed: Any = None, options: Any = None) -> gym.core.ObsType:
        """Options are ignored."""
        self.seed = seed
        self._reset_env()
        pixels = self.harness.get_screen()
        _ = self.noita_info.on_tick()
        # return pixels, info
        return pixels

    def run_info(self):
        return {"episode_step": self.ep_step, "environment_step": self.env_step}

    def _current_step_dir(self) -> str:
        chunk = self.env_step // 10000
        chunk_dir = f"{self.out_dir}/step_chunk_{chunk}"
        pathlib.Path(chunk_dir).mkdir(parents=True, exist_ok=True)
        return chunk_dir

    def _log_step(self, action: np.ndarray, step_val: StepVal):
        step_dir = self._current_step_dir()

        pixels_filename = step_dir + f"/{self.env_step}_pixels.jpg"
        action_filename = step_dir + f"/{self.env_step}_action.npy"
        reward_filename = step_dir + f"/{self.env_step}_reward.pkl"
        info_filename = step_dir + f"/{self.env_step}_info.pkl"
        step_filename = step_dir + f"/{self.env_step}_step.pkl"

        np.save(reward_filename, np.array([step_val.reward]))
        with open(action_filename, "wb") as f:
            pickle.dump(action, f)
        step_info = {
            "ep_step": self.ep_step,
            "ep_num": self.ep_num,
            "env_step": self.env_step,
            "terminated": step_val.terminated,
            "truncated": step_val.truncated,
        }
        # fmt: off
        with open(info_filename, "wb") as info_file, \
             open(step_filename, "wb") as step_file:
            # fmt: on
            pickle.dump(step_val.info, info_file)
            pickle.dump(step_info, step_file)
        if self.run_config["pixels_every_n_episodes"] > 0 and \
           self.ep_num % self.run_config["pixels_every_n_episodes"] == 0:
            with open(pixels_filename, "wb") as pixels_file:
                pixels_file.write(simplejpeg.encode_jpeg(step_val.pixels, quality=92))

    def close(self):
        self.harness.cleanup()
        del self.harness

    def pause(self):
        self.harness.keyboard.key_sequence(["Escape"])

    def resume(self):
        self.harness.keyboard.key_sequence(["Escape"])

    def _set_up_magic_numbers(self, seed):
        copy_comm = (
            (
                f"cp -f {project_root()}/bounce_rl/environments/noita/mod/files/magic_numbers_template.xml "
                f"{project_root()}/bounce_rl/environments/noita/mod/files/magic_numbers.xml"
            ),
        )
        print("Running:", copy_comm)
        subprocess.Popen(
            copy_comm,
            shell=True,
        )
        subprocess.Popen(
            f'sed -i s/SEED_HERE/\\"{seed}\\"/g {project_root()}/bounce_rl/environments/noita/mod/files/magic_numbers.xml',
            shell=True,
        )
