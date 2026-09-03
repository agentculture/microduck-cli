"""The duck-ipc-proto wire contract, transcribed into Python.

Source of truth: ``duck-ipc-proto/src/lib.rs`` in ``pollen-robotics/microduck``, pinned per
``docs/upstream-pins.md``:

    ref (commit):  0cd676d6fbb6e90a762c84aa63abe7a02dbc9495
    blob sha:      33224abe5be065f793904ba5a43380409f2cbd57

Only constant values and names are transcribed here -- never Rust code or doc comments.
See ``tests/fixtures/duck_ipc_proto.json`` for the fixture this table is tested against, and
``tests/test_proto.py`` for the table test.

Deviations from the t1 task brief, recorded here because they could not be resolved by
transcription (the brief anticipated shapes that do not exist in the pinned source, and
inventing them would defeat the point of pinning a source of truth):

* No ``policy.*`` or ``account.*`` method namespace exists in the pinned ``lib.rs``. Only
  ``hello`` and the ``robot.*`` / ``net.*`` / ``system.*`` / ``update.*`` / ``pad.*`` /
  ``tof.*`` / ``chorale.*`` namespaces are defined there, so that is what this module
  transcribes.
* No ``ROBOT_MODEL`` constant exists in the pinned source (only the ``robot.modelApi``
  *method* and its ``ModelApiResult.model_api`` version field). It is intentionally omitted
  here rather than fabricated.
* ``POLICY_ACTION_LEN`` is not a named constant in the pinned source either. It is derived
  from two things the source *does* state: ``JOINT_NAMES`` has 15 entries, and the
  ``robot.mouth`` doc comment says the mouth "is not part of any policy -- this is the only
  thing that moves it." 15 joints minus the one the policy never drives leaves 14, which
  matches the task brief's expected value, so it is recorded here as a derived constant
  rather than a literal transcription.
* ``robot.loadPolicy``, named in the task brief's discrete-method examples, does not exist
  as a method in the pinned source. ``is_notification`` still classifies it (and any other
  unrecognised method name) as a discrete request -- see its docstring -- so the brief's
  test case still holds without inventing a method constant for it.
"""

from __future__ import annotations

from types import MappingProxyType

# ── protocol / policy constants ────────────────────────────────────────────────

JSONRPC_VERSION = "2.0"
API_VERSION = 16

# The policy's observation is a flat 61-D vector (duck-ipc-proto/src/lib.rs, comment above
# `method::ROBOT_DO`: "the observation layout is the same 61-D vector throughout").
POLICY_OBS_LEN = 61

# Derived, not a literal constant in the pinned source -- see the module docstring's
# "Deviations" section: JOINT_NAMES (15) minus the mouth joint, which robot.mouth's doc says
# "is not part of any policy".
POLICY_ACTION_LEN = 14

# The robot's joint order, exactly as duck-ipc-proto/src/lib.rs::JOINT_NAMES.
# Left leg (5) - neck/head/mouth (5) - right leg (5).
JOINT_NAMES: tuple[str, ...] = (
    "left_hip_yaw",
    "left_hip_roll",
    "left_hip_pitch",
    "left_knee",
    "left_ankle",
    "neck_pitch",
    "head_pitch",
    "head_yaw",
    "head_roll",
    "mouth",
    "right_hip_yaw",
    "right_hip_roll",
    "right_hip_pitch",
    "right_knee",
    "right_ankle",
)

# ── default socket paths, per service (duck-ipc-proto/src/lib.rs::socket, DEFAULT_SOCKET) ──

DEFAULT_SOCKET = "/run/updaterd.sock"

SOCKET_UPDATER = DEFAULT_SOCKET
SOCKET_ROBOT = "/run/robotd.sock"
SOCKET_CONFIG = "/run/configd.sock"
SOCKET_PAD = "/run/padd/pad.sock"
SOCKET_TOF = "/run/tofd/tof.sock"

# ── method names, as they go on the wire (duck-ipc-proto/src/lib.rs::method) ────────────────

HELLO = "hello"

# update.*
UPDATE_CHECK = "update.check"
UPDATE_APPLY = "update.apply"
UPDATE_ROLLBACK = "update.rollback"
UPDATE_RESET_TO_GOLDEN = "update.resetToGolden"
UPDATE_SELECT = "update.select"
UPDATE_PIN = "update.pin"
UPDATE_STATUS = "update.status"
UPDATE_LIST_INSTALLED = "update.listInstalled"
UPDATE_LOG = "update.log"
UPDATE_SHOW = "update.show"
UPDATE_SUBSCRIBE = "update.subscribe"
UPDATE_PROGRESS = "update.progress"  # server -> client notification, never carries an id

# robot.* -- robotd's side, queried by updaterd
ROBOT_SAFE_TO_RESTART = "robot.safeToRestart"
ROBOT_HEALTH = "robot.health"
ROBOT_MODEL_API = "robot.modelApi"
ROBOT_SESSION_ACTIVE = "robot.remoteSessionActive"

# robot.* -- intents
ROBOT_MOVE = "robot.move"  # continuous; notification
ROBOT_HEAD = "robot.head"  # continuous; notification
ROBOT_LOOK = "robot.look"  # discrete; request
ROBOT_STOP = "robot.stop"  # discrete; request
ROBOT_ENABLE = "robot.enable"  # discrete; request

# robot.* -- power to the joints
ROBOT_INIT = "robot.init"  # discrete; request
ROBOT_RELAX = "robot.relax"  # discrete; request

# robot.* -- skills
ROBOT_DO = "robot.do"  # discrete; request
ROBOT_POSE = "robot.pose"  # continuous; notification
ROBOT_MOUTH = "robot.mouth"  # continuous; notification
ROBOT_SOUND = "robot.sound"  # continuous (hold semantics); notification
ROBOT_THEREMIN = "robot.theremin"  # discrete; request
ROBOT_CHORALE = "robot.chorale"  # discrete; request
ROBOT_SHUTDOWN = "robot.shutdown"  # discrete; request
ROBOT_MODE = "robot.mode"  # discrete; request
ROBOT_SET_MODE = "robot.setMode"  # discrete; request
ROBOT_SUBSCRIBE = "robot.subscribe"  # discrete; request
ROBOT_STATE = "robot.state"  # server -> client notification, never carries an id

# net.*
NET_STATUS = "net.status"
NET_SCAN = "net.scan"
NET_CONNECT = "net.connect"
NET_FORGET = "net.forget"

# system.*
SYSTEM_INFO = "system.info"
SYSTEM_SERVICES = "system.services"
SYSTEM_SET_NAME = "system.setName"
SYSTEM_REBOOT = "system.reboot"
SYSTEM_PAIRING_PIN = "system.pairingPin"
SYSTEM_SET_PAIRING_PIN = "system.setPairingPin"
SYSTEM_AUTHENTICATE = "system.authenticate"

# pad.*
PAD_STATUS = "pad.status"
PAD_PAIR = "pad.pair"
PAD_FORGET = "pad.forget"
PAD_INPUT = "pad.input"  # served by padd itself, on socket.PAD
PAD_REPORT = "pad.report"

# tof.*
TOF_STREAM = "tof.stream"  # served by tofd itself, on socket.TOF
TOF_FRAME = "tof.frame"  # notification, pushed after tof.stream

# chorale.*
CHORALE_SUBSCRIBE = "chorale.subscribe"
CHORALE_BEACON = "chorale.beacon"  # notification
CHORALE_HEARD = "chorale.heard"  # notification

# Every method name above, keyed by its Python constant name -- for table-driven tests and
# for anything that wants to enumerate the whole protocol surface.
METHODS: MappingProxyType[str, str] = MappingProxyType(
    {
        "HELLO": HELLO,
        "UPDATE_CHECK": UPDATE_CHECK,
        "UPDATE_APPLY": UPDATE_APPLY,
        "UPDATE_ROLLBACK": UPDATE_ROLLBACK,
        "UPDATE_RESET_TO_GOLDEN": UPDATE_RESET_TO_GOLDEN,
        "UPDATE_SELECT": UPDATE_SELECT,
        "UPDATE_PIN": UPDATE_PIN,
        "UPDATE_STATUS": UPDATE_STATUS,
        "UPDATE_LIST_INSTALLED": UPDATE_LIST_INSTALLED,
        "UPDATE_LOG": UPDATE_LOG,
        "UPDATE_SHOW": UPDATE_SHOW,
        "UPDATE_SUBSCRIBE": UPDATE_SUBSCRIBE,
        "UPDATE_PROGRESS": UPDATE_PROGRESS,
        "ROBOT_SAFE_TO_RESTART": ROBOT_SAFE_TO_RESTART,
        "ROBOT_HEALTH": ROBOT_HEALTH,
        "ROBOT_MODEL_API": ROBOT_MODEL_API,
        "ROBOT_SESSION_ACTIVE": ROBOT_SESSION_ACTIVE,
        "ROBOT_MOVE": ROBOT_MOVE,
        "ROBOT_HEAD": ROBOT_HEAD,
        "ROBOT_LOOK": ROBOT_LOOK,
        "ROBOT_STOP": ROBOT_STOP,
        "ROBOT_ENABLE": ROBOT_ENABLE,
        "ROBOT_INIT": ROBOT_INIT,
        "ROBOT_RELAX": ROBOT_RELAX,
        "ROBOT_DO": ROBOT_DO,
        "ROBOT_POSE": ROBOT_POSE,
        "ROBOT_MOUTH": ROBOT_MOUTH,
        "ROBOT_SOUND": ROBOT_SOUND,
        "ROBOT_THEREMIN": ROBOT_THEREMIN,
        "ROBOT_CHORALE": ROBOT_CHORALE,
        "ROBOT_SHUTDOWN": ROBOT_SHUTDOWN,
        "ROBOT_MODE": ROBOT_MODE,
        "ROBOT_SET_MODE": ROBOT_SET_MODE,
        "ROBOT_SUBSCRIBE": ROBOT_SUBSCRIBE,
        "ROBOT_STATE": ROBOT_STATE,
        "NET_STATUS": NET_STATUS,
        "NET_SCAN": NET_SCAN,
        "NET_CONNECT": NET_CONNECT,
        "NET_FORGET": NET_FORGET,
        "SYSTEM_INFO": SYSTEM_INFO,
        "SYSTEM_SERVICES": SYSTEM_SERVICES,
        "SYSTEM_SET_NAME": SYSTEM_SET_NAME,
        "SYSTEM_REBOOT": SYSTEM_REBOOT,
        "SYSTEM_PAIRING_PIN": SYSTEM_PAIRING_PIN,
        "SYSTEM_SET_PAIRING_PIN": SYSTEM_SET_PAIRING_PIN,
        "SYSTEM_AUTHENTICATE": SYSTEM_AUTHENTICATE,
        "PAD_STATUS": PAD_STATUS,
        "PAD_PAIR": PAD_PAIR,
        "PAD_FORGET": PAD_FORGET,
        "PAD_INPUT": PAD_INPUT,
        "PAD_REPORT": PAD_REPORT,
        "TOF_STREAM": TOF_STREAM,
        "TOF_FRAME": TOF_FRAME,
        "CHORALE_SUBSCRIBE": CHORALE_SUBSCRIBE,
        "CHORALE_BEACON": CHORALE_BEACON,
        "CHORALE_HEARD": CHORALE_HEARD,
    }
)

# ── error codes (duck-ipc-proto/src/lib.rs::code) ────────────────────────────────────────

# JSON-RPC 2.0 spec-reserved.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# Application-specific (duck/updater), in the private range.
BUSY = 1
UNKNOWN_COMPONENT = 2
PROTOCOL_MISMATCH = 3
PREFLIGHT_FAILED = 4
NETWORK = 5
VERIFICATION_FAILED = 6
INCOMPATIBLE = 7
HOOK_FAILED = 8
HEALTH_CHECK_FAILED = 9
ROLLBACK_FAILED = 10
NOT_INSTALLED = 11
WOULD_DOWNGRADE = 12
ARCHIVE_TOO_LARGE = 13
PERMISSION_DENIED = 14

ERROR_CODES: MappingProxyType[str, int] = MappingProxyType(
    {
        "PARSE_ERROR": PARSE_ERROR,
        "INVALID_REQUEST": INVALID_REQUEST,
        "METHOD_NOT_FOUND": METHOD_NOT_FOUND,
        "INVALID_PARAMS": INVALID_PARAMS,
        "INTERNAL_ERROR": INTERNAL_ERROR,
        "BUSY": BUSY,
        "UNKNOWN_COMPONENT": UNKNOWN_COMPONENT,
        "PROTOCOL_MISMATCH": PROTOCOL_MISMATCH,
        "PREFLIGHT_FAILED": PREFLIGHT_FAILED,
        "NETWORK": NETWORK,
        "VERIFICATION_FAILED": VERIFICATION_FAILED,
        "INCOMPATIBLE": INCOMPATIBLE,
        "HOOK_FAILED": HOOK_FAILED,
        "HEALTH_CHECK_FAILED": HEALTH_CHECK_FAILED,
        "ROLLBACK_FAILED": ROLLBACK_FAILED,
        "NOT_INSTALLED": NOT_INSTALLED,
        "WOULD_DOWNGRADE": WOULD_DOWNGRADE,
        "ARCHIVE_TOO_LARGE": ARCHIVE_TOO_LARGE,
        "PERMISSION_DENIED": PERMISSION_DENIED,
    }
)

# ── continuous (notification) vs discrete (request) classification ──────────────────────

# The continuous intents: sent as JSON-RPC notifications (no id), last-writer-wins, at rate.
# robot.sound is included for its held ride (`hold: true`/`hold: false`), which duck-ipc-proto
# documents as "a notification per tick, like the mouth".
NOTIFICATION_METHODS: frozenset[str] = frozenset(
    {
        ROBOT_MOVE,
        ROBOT_HEAD,
        ROBOT_POSE,
        ROBOT_MOUTH,
        ROBOT_SOUND,
    }
)


def is_notification(method: str) -> bool:
    """Return True if `method` is sent as a JSON-RPC notification (no id), False otherwise.

    True only for the continuous intents in NOTIFICATION_METHODS (robot.move, robot.head,
    robot.pose, robot.mouth, robot.sound). Every discrete request (robot.do, robot.look,
    robot.stop, robot.enable, robot.init, robot.relax, robot.setMode, and any other
    non-robot method, known or not) classifies as False -- an unrecognised method name is a
    discrete request by default, never a notification, since guessing the wrong way here
    would mean sending a call that expects a reply with none ever arriving.
    """
    return method in NOTIFICATION_METHODS
