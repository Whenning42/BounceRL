dofile( "data/scripts/lib/coroutines.lua" )

--[[
rl_mod is a Noita mod that logs game state to files for direct use, or
for use with the Noita Gym environment.

Note: Even though this project is Linux only, this mod is running under Wine.
This means lua os.execute calls don't work as expected.

Logged files are:
  /tmp/.../noita_stats.txt - Contains: biome, hp, max_hp, gold, x, y logged at the configured rate
  /tmp/.../noita_notifications.txt - Updated each time the player dies
]]

-- TODO: Make the config loadable from the pipe directory. This would allow configuration from
-- the Gym env.

math.randomseed(os.time())
PIPE_DIR = os.getenv("ENV_PREFIX")
STATS_EVERY_N_FRAMES = 2
print(" ======== Piping output to: " .. PIPE_DIR .. " ========")

function GetPlayer()
    local players = EntityGetWithTag("player_unit")
    if #players == 0 then return end
    return players[1]
end

function GetPlayerOrCameraPos()
    local player = GetPlayer()
    local x, y = EntityGetTransform(player)
    if x == nil then
        return GameGetCameraPos()
    end
    return x, y
end

function LogStats()
    local player = GetPlayer()

    -- Get position and biome
    local x, y = GetPlayerOrCameraPos()
    local biome = BiomeMapGetName(x, y)

    -- Get tick
    local tick_id = GameGetFrameNum()

    local hp = 0
    local max_hp = 0
    local gold = 0
    local polymorphed = 0

    -- Get player stats
    if player == nil then
        polymorphed = 1
    else
        -- Get HP
        local damage_comp = EntityGetFirstComponent(player, "DamageModelComponent")
        if damage_comp ~= nil then
            hp = 25 * ComponentGetValue(damage_comp, "hp")
            max_hp = 25 * ComponentGetValue(damage_comp, "max_hp")
        end

        -- Get gold
        local wallet = EntityGetFirstComponent(player, "WalletComponent")
        if wallet ~= nil then 
            gold = ComponentGetValue(wallet, "money") + ComponentGetValue(wallet, "money_spent")
        end
    end

    -- Write to file
    local file = io.open(PIPE_DIR .. "/noita_stats.tsv", "a")
    -- Keep in sync with noita_info.py.
    file:write(string.format("%s\t%d\t%d\t%d\t%d\t%d\t%d\t%d\n", biome, hp, max_hp, gold, x, y, tick_id, polymorphed))
    file:close()
end

function OnWorldPostUpdate()
    local frame = GameGetFrameNum()
    if frame % STATS_EVERY_N_FRAMES == 0 then
        print("====== Stat log ======")
        LogStats()
    end
end

function OnPlayerDied(player)
    -- Log player death signal. The Gym env will recieve the signal and end the run.
    local file = io.open(PIPE_DIR .. "/noita_notifications.txt", "a")
    file:write("died\n")
    file:close()
end

function LogStr(str)
    local file = io.open(PIPE_DIR .. "/noita_mod_log.txt", "a")
    file:write(str .. "\n")
    file:close()
end

local file = io.open(PIPE_DIR .. "/noita_stats.tsv", "a")
-- The noita mod assumes every line is a values line.
-- file:write("biome, hp, max_hp, gold, x, y\n")
file:close()

ModMagicNumbersFileAdd( "mods/rl_mod/files/magic_numbers.xml" )
