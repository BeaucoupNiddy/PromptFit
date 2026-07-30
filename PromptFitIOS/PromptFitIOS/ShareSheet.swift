import SwiftUI
#if canImport(UIKit)
import UIKit

struct ShareSheet: UIViewControllerRepresentable {
    let items: [Any]

    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: items, applicationActivities: nil)
    }

    func updateUIViewController(_ uiViewController: UIActivityViewController, context: Context) {}
}
#elseif canImport(AppKit)
import AppKit

// This branch exists only so the source can be checked on a Mac that does not
// yet have the iOS SDK. The app target itself builds the UIKit branch above.
struct ShareSheet: NSViewControllerRepresentable {
    let items: [Any]

    func makeNSViewController(context: Context) -> NSViewController { NSViewController() }
    func updateNSViewController(_ nsViewController: NSViewController, context: Context) {}
}
#endif
