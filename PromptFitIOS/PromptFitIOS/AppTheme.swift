import SwiftUI

enum AppTheme {
    static let forest = Color(red: 0.035, green: 0.235, blue: 0.19)
    static let deepForest = Color(red: 0.018, green: 0.145, blue: 0.12)
    static let lime = Color(red: 0.79, green: 0.96, blue: 0.35)
    static let cream = Color(red: 0.965, green: 0.95, blue: 0.91)
    static let paper = Color(red: 0.99, green: 0.985, blue: 0.97)
    static let muted = Color(red: 0.36, green: 0.40, blue: 0.39)
    static let success = Color(red: 0.06, green: 0.49, blue: 0.36)
}

struct CardModifier: ViewModifier {
    func body(content: Content) -> some View {
        content
            .padding(18)
            .background(AppTheme.paper)
            .clipShape(RoundedRectangle(cornerRadius: 22, style: .continuous))
            .overlay {
                RoundedRectangle(cornerRadius: 22, style: .continuous)
                    .stroke(Color.black.opacity(0.07), lineWidth: 1)
            }
    }
}

extension View {
    func promptFitCard() -> some View { modifier(CardModifier()) }
}

struct PrimaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.headline)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 15)
            .foregroundStyle(AppTheme.deepForest)
            .background(AppTheme.lime.opacity(configuration.isPressed ? 0.75 : 1))
            .clipShape(Capsule())
            .scaleEffect(configuration.isPressed ? 0.985 : 1)
    }
}

struct SecondaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.headline)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 14)
            .foregroundStyle(AppTheme.forest)
            .background(AppTheme.forest.opacity(configuration.isPressed ? 0.16 : 0.09))
            .clipShape(Capsule())
    }
}
