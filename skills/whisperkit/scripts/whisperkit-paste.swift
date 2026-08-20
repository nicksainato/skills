import ApplicationServices
import Foundation

let promptForPermission = !CommandLine.arguments.contains("--no-prompt")
let trustOptions = [
    kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: promptForPermission
] as CFDictionary

guard AXIsProcessTrustedWithOptions(trustOptions) else {
    FileHandle.standardError.write(
        Data("WhisperKit Paste needs Accessibility permission.\n".utf8)
    )
    exit(2)
}

if CommandLine.arguments.contains("--check") {
    exit(0)
}

guard let source = CGEventSource(stateID: .hidSystemState),
      let keyDown = CGEvent(keyboardEventSource: source, virtualKey: 0x09, keyDown: true),
      let keyUp = CGEvent(keyboardEventSource: source, virtualKey: 0x09, keyDown: false) else {
    FileHandle.standardError.write(Data("Could not create paste events.\n".utf8))
    exit(1)
}

keyDown.flags = .maskCommand
keyUp.flags = .maskCommand
keyDown.post(tap: .cghidEventTap)
Thread.sleep(forTimeInterval: 0.01)
keyUp.post(tap: .cghidEventTap)
