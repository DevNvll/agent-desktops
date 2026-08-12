local M = {}

M.output_prefix = "AGENT-"
M.base_workspace_prefix = "__agent-base:"
M.special_workspace_prefix = "special:agent:"
M.max_desktops = 16

-- Agent screenshots use grim against one named headless output. Hyprland still
-- applies no_screen_share window rules to sensitive windows.
hl.permission("/usr/bin/grim", "screencopy", "allow")

-- Keep every possible managed output away from the physical layout across
-- configuration reloads. A later exact rule sets the requested resolution.
for slot = 0, M.max_desktops - 1 do
    hl.monitor({
        output = M.output_prefix .. tostring(slot),
        mode = "preferred",
        position = tostring(100000 + slot * 8192) .. "x100000",
        scale = 1,
    })
end

local function starts_with(value, prefix)
    return type(value) == "string" and value:sub(1, #prefix) == prefix
end

function M.is_agent_monitor(monitor)
    return monitor ~= nil and starts_with(monitor.name, M.output_prefix)
end

function M.is_human_workspace(workspace)
    if workspace == nil or workspace.special then
        return false
    end
    if starts_with(workspace.name, M.base_workspace_prefix) then
        return false
    end
    return not M.is_agent_monitor(workspace.monitor)
end

local function workspace_selector(workspace)
    if workspace.id ~= nil and workspace.id > 0 then
        return tostring(workspace.id)
    end
    return "name:" .. workspace.name
end

local function agent_special_active()
    local special = hl.get_active_special_workspace()
    return special ~= nil and starts_with(special.name, M.special_workspace_prefix)
end

function M.bind_human_workspace(workspace, move_window)
    return function()
        if agent_special_active() then
            return
        end
        if move_window then
            hl.dispatch(hl.dsp.window.move({ workspace = workspace }))
        else
            hl.dispatch(hl.dsp.focus({ workspace = workspace }))
        end
    end
end

function M.bind_human_special(workspace, move_window)
    return function()
        if agent_special_active() then
            return
        end
        if move_window then
            hl.dispatch(hl.dsp.window.move({ workspace = "special:" .. workspace }))
        else
            hl.dispatch(hl.dsp.workspace.toggle_special(workspace))
        end
    end
end

function M.human_workspaces()
    local result = {}
    for _, workspace in ipairs(hl.get_workspaces()) do
        if M.is_human_workspace(workspace) then
            table.insert(result, workspace)
        end
    end

    table.sort(result, function(left, right)
        if left.id ~= nil and right.id ~= nil and left.id ~= right.id then
            return left.id < right.id
        end
        return tostring(left.name or "") < tostring(right.name or "")
    end)
    return result
end

local function relative_workspace(delta)
    local workspaces = M.human_workspaces()
    if #workspaces == 0 then
        return nil
    end

    local active = hl.get_active_workspace()
    local active_index = nil
    for index, workspace in ipairs(workspaces) do
        if active ~= nil and workspace.id == active.id then
            active_index = index
            break
        end
    end

    if active_index == nil then
        return workspaces[delta > 0 and 1 or #workspaces]
    end

    local offset = delta > 0 and 1 or -1
    local next_index = ((active_index - 1 + offset) % #workspaces) + 1
    return workspaces[next_index]
end

local function change_relative_workspace(delta, move_window)
    if agent_special_active() then
        return
    end

    local target = relative_workspace(delta)
    if target == nil then
        return
    end

    local selector = workspace_selector(target)
    if move_window then
        hl.dispatch(hl.dsp.window.move({ workspace = selector }))
    else
        hl.dispatch(hl.dsp.focus({ workspace = selector }))
    end
end

function M.bind_relative_workspace(delta, move_window)
    return function()
        change_relative_workspace(delta, move_window == true)
    end
end

function M.bind_previous_human_workspace()
    return function()
        if agent_special_active() then
            return
        end

        local active_monitor = hl.get_active_monitor()
        if active_monitor == nil or M.is_agent_monitor(active_monitor) then
            return
        end

        local previous = hl.get_last_workspace(active_monitor)
        if not M.is_human_workspace(previous) then
            return
        end

        hl.dispatch(hl.dsp.focus({ workspace = workspace_selector(previous) }))
    end
end

local function monitor_center(monitor)
    return {
        x = monitor.x + monitor.width / 2,
        y = monitor.y + monitor.height / 2,
    }
end

local function monitor_in_direction(direction, move_window)
    local active = hl.get_active_monitor()
    if active == nil then
        return
    end

    if M.is_agent_monitor(active) then
        for _, monitor in ipairs(hl.get_monitors()) do
            if not M.is_agent_monitor(monitor) then
                hl.dispatch(hl.dsp.focus({ monitor = monitor.name }))
                return
            end
        end
        return
    end

    local origin = monitor_center(active)
    local best = nil
    local best_score = nil

    for _, monitor in ipairs(hl.get_monitors()) do
        if monitor.id ~= active.id and not M.is_agent_monitor(monitor) then
            local candidate = monitor_center(monitor)
            local dx = candidate.x - origin.x
            local dy = candidate.y - origin.y
            local primary = nil
            local secondary = nil

            if direction == "left" and dx < 0 then
                primary, secondary = -dx, math.abs(dy)
            elseif direction == "right" and dx > 0 then
                primary, secondary = dx, math.abs(dy)
            elseif direction == "up" and dy < 0 then
                primary, secondary = -dy, math.abs(dx)
            elseif direction == "down" and dy > 0 then
                primary, secondary = dy, math.abs(dx)
            end

            if primary ~= nil then
                local score = primary + secondary * 4
                if best_score == nil or score < best_score then
                    best = monitor
                    best_score = score
                end
            end
        end
    end

    if best == nil then
        return
    end

    if move_window then
        hl.dispatch(hl.dsp.window.move({ monitor = best.name }))
    else
        hl.dispatch(hl.dsp.focus({ monitor = best.name }))
    end
end

function M.bind_monitor(direction, move_window)
    return function()
        monitor_in_direction(direction, move_window == true)
    end
end

function M.configure_output(output, mode, slot)
    if not starts_with(output, M.output_prefix) then
        error("agent output names must start with " .. M.output_prefix)
    end

    local index = math.max(0, math.floor(tonumber(slot) or 0))
    hl.monitor({
        output = M.output_prefix .. tostring(index),
        mode = mode,
        position = tostring(100000 + index * 8192) .. "x100000",
        scale = 1,
    })
end

-- hyprctl eval uses this table for dynamic output rules. The native plug-in
-- adds hl.plugin.agent_desktops_v3 for in-process callers.
_G.AgentDesktops = M

return M
