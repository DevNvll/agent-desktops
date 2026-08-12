#include <hyprland/src/desktop/Workspace.hpp>
#include <hyprland/src/desktop/state/FocusState.hpp>
#include <hyprland/src/desktop/state/WindowState.hpp>
#include <hyprland/src/desktop/view/Window.hpp>
#include <hyprland/src/output/Monitor.hpp>
#include <hyprland/src/plugins/PluginAPI.hpp>
#include <hyprland/src/pointer/PointerManager.hpp>
#include <hyprland/src/state/MonitorState.hpp>
#include <hyprland/src/state/WorkspacePlacementController.hpp>
#include <hyprland/src/state/WorkspaceState.hpp>
#include <aquamarine/backend/Backend.hpp>

extern "C" {
#include <lauxlib.h>
#include <lua.h>
}

#include <algorithm>
#include <cctype>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <unordered_set>
#include <utility>

namespace {

HANDLE pluginHandle = nullptr;
std::unordered_set<std::string> removalsInProgress;

constexpr std::string_view SPECIAL_PREFIX = "special:agent:";
constexpr std::string_view BASE_PREFIX = "__agent-base:";

struct AgentArgs {
  std::string id;
  std::string monitor;
};

struct FocusSnapshot {
  PHLMONITOR monitor;
  PHLWORKSPACE workspace;
  PHLWINDOW window;
};

class RemovalGuard {
 public:
  explicit RemovalGuard(std::string outputName)
      : name(std::move(outputName)),
        acquired(removalsInProgress.insert(name).second) {}

  ~RemovalGuard() {
    if (acquired)
      removalsInProgress.erase(name);
  }

  bool ownsRemoval() const { return acquired; }

 private:
  std::string name;
  bool acquired;
};

SDispatchResult failure(std::string message) {
  return {.success = false, .error = std::move(message)};
}

bool validID(const std::string_view id) {
  if (id.empty() || id.size() > 40 ||
      !std::isalnum(static_cast<unsigned char>(id.front())))
    return false;

  return std::ranges::all_of(id, [](const char value) {
    const auto byte = static_cast<unsigned char>(value);
    return std::islower(byte) || std::isdigit(byte) || value == '-';
  });
}

std::pair<AgentArgs, std::string> parseArgs(const std::string &raw) {
  AgentArgs args;
  std::string extra;
  std::istringstream stream(raw);
  stream >> args.id >> args.monitor >> extra;

  if (!validID(args.id))
    return {{},
            "the desktop ID must use lower-case letters, digits, and hyphens"};
  if (args.monitor.empty() || !extra.empty())
    return {{}, "expected: <desktop-id> <monitor-name>"};
  if (args.monitor.size() > 128)
    return {{}, "the monitor name is too long"};

  return {std::move(args), {}};
}

PHLMONITOR findMonitor(const std::string &name) {
  return State::monitorState()->query().name(name).run();
}

PHLMONITOR findAnyMonitor(const std::string &name) {
  return State::monitorState()->query().includeDisabled(true).name(name).run();
}

PHLWORKSPACE findWorkspace(const std::string &name) {
  return State::workspaceState()->query().name(name).run();
}

FocusSnapshot captureSafeFocus(const PHLWORKSPACE &movingSpecial) {
  FocusSnapshot snapshot = {
      .monitor = Desktop::focusState()->monitor(),
      .workspace = nullptr,
      .window = Desktop::focusState()->window(),
  };

  if (!snapshot.monitor || snapshot.monitor->m_name.starts_with("AGENT-")) {
    snapshot.monitor = nullptr;
    snapshot.workspace = nullptr;
    snapshot.window = nullptr;
    for (const auto &candidate : State::monitorState()->monitors()) {
      if (!candidate->m_name.starts_with("AGENT-")) {
        snapshot.monitor = candidate;
        break;
      }
    }
  }

  if (snapshot.monitor)
    snapshot.workspace = snapshot.monitor->m_activeWorkspace;

  if (snapshot.monitor &&
      (!snapshot.window ||
       (movingSpecial && snapshot.window->m_workspace == movingSpecial))) {
    snapshot.window = snapshot.monitor->m_activeWorkspace
                          ? snapshot.monitor->m_activeWorkspace
                                ->getLastFocusedWindow()
                          : nullptr;
  }

  return snapshot;
}

void restoreFocus(const FocusSnapshot &snapshot) {
  if (snapshot.monitor && snapshot.workspace &&
      snapshot.workspace->m_monitor == snapshot.monitor &&
      snapshot.monitor->m_activeWorkspace != snapshot.workspace)
    snapshot.monitor->changeWorkspace(snapshot.workspace, true, true, true);

  if (snapshot.monitor)
    Desktop::focusState()->rawMonitorFocus(snapshot.monitor);

  if (snapshot.window)
    Desktop::focusState()->fullWindowFocus(snapshot.window,
                                           Desktop::FOCUS_REASON_OTHER);
  else if (snapshot.monitor) {
    Desktop::focusState()->fullWindowFocus(nullptr,
                                           Desktop::FOCUS_REASON_OTHER);
  }
}

PHLWORKSPACE ensureBaseWorkspace(const AgentArgs &args,
                                 const PHLMONITOR &monitor) {
  const auto name = std::string(BASE_PREFIX) + args.id;
  auto workspace = findWorkspace(name);

  if (!workspace) {
    workspace = State::workspaceState()->create(
        State::workspaceState()->nextAvailableNamedWorkspace(), monitor->m_id,
        name, true);
  }

  if (workspace && workspace->m_monitor != monitor)
    State::workspacePlacementController()->moveWorkspaceToMonitor(
        workspace, monitor, true);

  if (workspace)
    workspace->setPersistent(true);
  return workspace;
}

PHLWORKSPACE ensureSpecialWorkspace(const AgentArgs &args,
                                    const PHLMONITOR &monitor) {
  const auto name = std::string(SPECIAL_PREFIX) + args.id;
  auto workspace = findWorkspace(name);

  if (!workspace) {
    workspace = State::workspaceState()->create(
        State::workspaceState()->newSpecialID(), monitor->m_id, name, true);
  }

  if (workspace)
    workspace->setPersistent(true);
  return workspace;
}

SDispatchResult placeWorkspace(const AgentArgs &args, const bool prepareBase) {
  const auto monitor = findMonitor(args.monitor);
  if (!monitor)
    return failure("agent monitor not found: " + args.monitor);

  const bool preserveFocus = monitor->m_name.starts_with("AGENT-");
  const auto previousFocus =
      preserveFocus
          ? captureSafeFocus(findWorkspace(std::string(SPECIAL_PREFIX) + args.id))
          : FocusSnapshot{};

  const auto existingSpecial =
      findWorkspace(std::string(SPECIAL_PREFIX) + args.id);
  if (monitor->m_activeSpecialWorkspace &&
      monitor->m_activeSpecialWorkspace != existingSpecial)
    return failure("the target monitor already has another special workspace");

  const auto special = ensureSpecialWorkspace(args, monitor);
  if (!special || !special->m_isSpecialWorkspace)
    return failure("could not create the agent special workspace");

  if (prepareBase) {
    const auto base = ensureBaseWorkspace(args, monitor);
    if (!base)
      return failure("could not create the agent base workspace");
    monitor->changeWorkspace(base, true, true, true);
  }

  monitor->setSpecialWorkspace(special);
  monitor->scheduleFrame(Aquamarine::IOutput::AQ_SCHEDULE_DAMAGE);

  if (preserveFocus)
    restoreFocus(previousFocus);

  return {};
}

SDispatchResult prepare(const std::string &raw) {
  auto [args, error] = parseArgs(raw);
  if (!error.empty())
    return failure(error);
  if (!args.monitor.starts_with("AGENT-"))
    return failure("the agent monitor name must start with AGENT-");
  return placeWorkspace(args, true);
}

SDispatchResult place(const std::string &raw) {
  auto [args, error] = parseArgs(raw);
  if (!error.empty())
    return failure(error);
  return placeWorkspace(args, false);
}

SDispatchResult cleanup(const std::string &raw) {
  auto [args, error] = parseArgs(raw);
  if (!error.empty())
    return failure(error);
  if (!args.monitor.starts_with("AGENT-"))
    return failure("the agent monitor name must start with AGENT-");

  const auto specialName = std::string(SPECIAL_PREFIX) + args.id;
  const auto special = findWorkspace(specialName);
  const auto base = findWorkspace(std::string(BASE_PREFIX) + args.id);

  if (special && special->getWindowCount() > 0)
    return failure("close all windows before desktop removal");
  if (base && base->getWindowCount() > 0)
    return failure("the agent base workspace still has windows");

  if (special) {
    const auto previousFocus = captureSafeFocus(special);
    for (const auto &monitor : State::monitorState()->monitors()) {
      if (monitor->m_activeSpecialWorkspace == special)
        monitor->setSpecialWorkspace(nullptr);
    }
    restoreFocus(previousFocus);
    special->setPersistent(false);
  }

  if (base)
    base->setPersistent(false);

  return {};
}

Vector2D safeCursorPosition(const FocusSnapshot &focus,
                            const Vector2D &previous) {
  for (const auto &candidate : State::monitorState()->monitors()) {
    if (!candidate || candidate->m_name.starts_with("AGENT-"))
      continue;
    const auto &position = candidate->m_position;
    const auto &size = candidate->m_size;
    if (previous.x >= position.x && previous.y >= position.y &&
        previous.x < position.x + size.x &&
        previous.y < position.y + size.y)
      return previous;
  }

  if (focus.monitor)
    return focus.monitor->m_position + focus.monitor->m_size / 2.0;
  return previous;
}

SDispatchResult validateRemovalState(const AgentArgs &args,
                                     const PHLMONITOR &monitor,
                                     const PHLWORKSPACE &special,
                                     const PHLWORKSPACE &base,
                                     const bool allowInitialWorkspace) {
  const auto specialName = std::string(SPECIAL_PREFIX) + args.id;
  const auto baseName = std::string(BASE_PREFIX) + args.id;

  for (const auto &window : Desktop::windowState()->windows()) {
    if (window &&
        (window->monitorID() == monitor->m_id ||
         (window->m_workspace && window->m_workspace->m_monitor == monitor)))
      return failure("close all windows before desktop removal");
  }

  for (const auto &workspace : State::workspaceState()->workspaces()) {
    if (!workspace)
      continue;
    const bool managed =
        workspace->m_name == specialName || workspace->m_name == baseName;
    if (workspace->m_monitor == monitor && !managed) {
      if (!allowInitialWorkspace || workspace->isPersistent() ||
          workspace->getWindowCount() > 0)
        return failure("the agent monitor has an unmanaged workspace");
    }
    if (managed && workspace->m_monitor != monitor)
      return failure("an agent workspace is on another monitor");
  }

  if ((special && special->getWindowCount() > 0) ||
      (base && base->getWindowCount() > 0))
    return failure("close all windows before desktop removal");

  return {};
}

SDispatchResult validateRemovalOutput(
    const AgentArgs &args, const PHLMONITOR &monitor,
    const SP<Aquamarine::IOutput> &output) {
  if (!monitor || !output || findAnyMonitor(args.monitor) != monitor ||
      monitor->m_output != output)
    return failure("the agent output changed during removal");
  if (!monitor->m_createdByUser || monitor->m_isUnsafeFallback)
    return failure("refusing to remove a physical monitor");

  const auto backend = output->getBackend();
  if (!backend || backend->type() != Aquamarine::AQ_BACKEND_HEADLESS)
    return failure("refusing to remove a non-headless output");
  if (monitor->m_mirrorOf ||
      std::ranges::any_of(monitor->m_mirrors, [](const auto &mirror) {
        return static_cast<bool>(mirror);
      }))
    return failure("refusing to remove a mirrored agent output");

  const bool hasPhysicalBackup =
      std::ranges::any_of(State::monitorState()->monitors(),
                          [&](const auto &candidate) {
                            return candidate && candidate != monitor &&
                                   !candidate->m_name.starts_with("AGENT-") &&
                                   !candidate->m_isUnsafeFallback;
                          });
  if (!hasPhysicalBackup)
    return failure("an active physical monitor is required for removal");

  return {};
}

SDispatchResult removeOutput(const std::string &raw,
                             const bool allowInitialWorkspace = false) {
  auto [args, error] = parseArgs(raw);
  if (!error.empty())
    return failure(error);
  if (!args.monitor.starts_with("AGENT-"))
    return failure("the agent monitor name must start with AGENT-");

  RemovalGuard removalGuard(args.monitor);
  if (!removalGuard.ownsRemoval())
    return failure("agent output removal is already in progress");

  auto monitor = findAnyMonitor(args.monitor);
  if (!monitor)
    return failure("agent monitor not found: " + args.monitor);
  auto output = monitor->m_output;
  const auto initialOutputValidation =
      validateRemovalOutput(args, monitor, output);
  if (!initialOutputValidation.success)
    return initialOutputValidation;

  const auto specialName = std::string(SPECIAL_PREFIX) + args.id;
  const auto baseName = std::string(BASE_PREFIX) + args.id;
  const auto special = findWorkspace(specialName);
  const auto base = findWorkspace(baseName);

  const auto initialValidation = validateRemovalState(
      args, monitor, special, base, allowInitialWorkspace);
  if (!initialValidation.success)
    return initialValidation;

  const auto previousFocus = captureSafeFocus(special);
  const auto previousCursor = Pointer::mgr()->position();
  const auto safeCursor = safeCursorPosition(previousFocus, previousCursor);

  if (special) {
    const auto monitors = State::monitorState()->monitors();
    for (const auto &candidate : monitors) {
      if (candidate->m_activeSpecialWorkspace == special)
        candidate->setSpecialWorkspace(nullptr);
    }
  }

  restoreFocus(previousFocus);
  Pointer::mgr()->warpTo(safeCursor);

  const auto finalValidation = validateRemovalState(
      args, monitor, special, base, allowInitialWorkspace);
  if (!finalValidation.success)
    return finalValidation;
  const auto finalOutputValidation =
      validateRemovalOutput(args, monitor, output);
  if (!finalOutputValidation.success)
    return finalOutputValidation;

  if (special)
    special->setPersistent(false);
  if (base)
    base->setPersistent(false);

  const bool destroyed = output->destroy();
  restoreFocus(previousFocus);
  Pointer::mgr()->warpTo(safeCursor);
  if (!destroyed) {
    return failure("Hyprland could not remove the agent output");
  }

  monitor.reset();
  output.reset();
  return {};
}

SDispatchResult abortOutput(const std::string &raw) {
  return removeOutput(raw, true);
}

SDispatchResult removeManagedOutput(const std::string &raw) {
  return removeOutput(raw, false);
}

int pushLuaResult(lua_State *state, const SDispatchResult &result) {
  if (!result.success) {
    lua_pushnil(state);
    lua_pushlstring(state, result.error.data(), result.error.size());
    return 2;
  }

  lua_pushboolean(state, true);
  return 1;
}

std::string luaArgs(lua_State *state) {
  const auto *id = luaL_checkstring(state, 1);
  const auto *monitor = luaL_checkstring(state, 2);
  return std::string(id) + " " + monitor;
}

int luaPrepare(lua_State *state) {
  return pushLuaResult(state, prepare(luaArgs(state)));
}

int luaPlace(lua_State *state) {
  return pushLuaResult(state, place(luaArgs(state)));
}

int luaCleanup(lua_State *state) {
  return pushLuaResult(state, cleanup(luaArgs(state)));
}

int luaRemove(lua_State *state) {
  return pushLuaResult(state, removeManagedOutput(luaArgs(state)));
}

int luaAbort(lua_State *state) {
  return pushLuaResult(state, abortOutput(luaArgs(state)));
}

void registerDispatcher(const std::string &name,
                        std::function<SDispatchResult(std::string)> handler) {
  if (!HyprlandAPI::addDispatcherV2(pluginHandle, name, std::move(handler)))
    throw std::runtime_error("could not register dispatcher: " + name);
}

} // namespace

APICALL EXPORT std::string PLUGIN_API_VERSION() { return HYPRLAND_API_VERSION; }

APICALL EXPORT PLUGIN_DESCRIPTION_INFO PLUGIN_INIT(HANDLE handle) {
  pluginHandle = handle;

  if (std::string(__hyprland_api_get_hash()) !=
      __hyprland_api_get_client_hash())
    throw std::runtime_error(
        "agent-desktops was built for a different Hyprland ABI");

  registerDispatcher("agentdesktopv3prepare", prepare);
  registerDispatcher("agentdesktopv3place", place);
  registerDispatcher("agentdesktopv3cleanup", cleanup);
  registerDispatcher("agentdesktopv3remove", removeManagedOutput);
  registerDispatcher("agentdesktopv3abort", abortOutput);

  if (!HyprlandAPI::addLuaFunction(pluginHandle, "agent_desktops_v3", "prepare",
                                   luaPrepare) ||
      !HyprlandAPI::addLuaFunction(pluginHandle, "agent_desktops_v3", "place",
                                   luaPlace) ||
      !HyprlandAPI::addLuaFunction(pluginHandle, "agent_desktops_v3", "cleanup",
                                   luaCleanup) ||
      !HyprlandAPI::addLuaFunction(pluginHandle, "agent_desktops_v3", "remove",
                                   luaRemove) ||
      !HyprlandAPI::addLuaFunction(pluginHandle, "agent_desktops_v3", "abort",
                                   luaAbort))
    throw std::runtime_error("could not register agent desktop Lua functions");

  return {
      .name = "agent-desktops-v3",
      .description = "Isolated headless workspaces for GUI automation agents",
      .author = "nullskies",
      .version = "0.1.0",
  };
}

APICALL EXPORT void PLUGIN_EXIT() { pluginHandle = nullptr; }
