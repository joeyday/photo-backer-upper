// Tiny launcher so macOS privacy permissions (Photos, and Full Disk Access if ever needed)
// are granted to this app alone, not to a shared Python interpreter.
//
// Two ways it runs:
//   * No arguments (double-clicked in Finder): asks for Photos access, then runs
//     `pback export --dry-run` as a harmless check.
//   * With arguments (launchd): runs `pback <arguments>` and exits with its status.
//
// It never unmounts anything and does nothing but start pback.

import Foundation
import Photos

func log(_ s: String) {
    FileHandle.standardError.write(Data((s + "\n").utf8))
}

// Ask for Photos access. Shows the system prompt the first time when launched from Finder;
// when launched headlessly (launchd) it can't prompt and just reports the current status.
// Waits at most 10 minutes for an answer, so a prompt nobody sees can't hang every scheduled run.
func photosStatus() -> PHAuthorizationStatus {
    let current = PHPhotoLibrary.authorizationStatus(for: .readWrite)
    if current != .notDetermined { return current }
    let sema = DispatchSemaphore(value: 0)
    var result = current
    PHPhotoLibrary.requestAuthorization(for: .readWrite) { status in
        result = status
        sema.signal()
    }
    if sema.wait(timeout: .now() + 600) == .timedOut {
        log("No answer to the Photos access request after 10 minutes; carrying on without it.")
        return PHPhotoLibrary.authorizationStatus(for: .readWrite)
    }
    return result
}

let status = photosStatus()
log("Photos access status: \(status.rawValue) (3 = authorized, 2 = denied, 1 = restricted, 4 = limited)")
if status != .authorized {
    log("Photos access is not 'All Photos'; osxphotos may not see the whole library.")
}

let home = FileManager.default.homeDirectoryForCurrentUser.path
let pback = "\(home)/.local/bin/pback"
guard FileManager.default.isExecutableFile(atPath: pback) else {
    log("pback not found at \(pback); install it with: uv tool install .")
    exit(127)
}

var args = Array(CommandLine.arguments.dropFirst())
// Finder passes a process-serial-number argument on some macOS versions; ignore it.
args = args.filter { !$0.hasPrefix("-psn_") }
if args.isEmpty { args = ["export", "--dry-run"] }

let task = Process()
task.executableURL = URL(fileURLWithPath: pback)
task.arguments = args
var env = ProcessInfo.processInfo.environment
env["PATH"] = ["\(home)/.local/bin", "/opt/homebrew/bin", "/usr/local/bin",
               "/usr/bin", "/bin", "/usr/sbin", "/sbin"].joined(separator: ":")
env["PYTHONUNBUFFERED"] = "1"
task.environment = env

do {
    try task.run()
} catch {
    log("Could not start pback: \(error)")
    exit(126)
}
task.waitUntilExit()
exit(task.terminationStatus)
