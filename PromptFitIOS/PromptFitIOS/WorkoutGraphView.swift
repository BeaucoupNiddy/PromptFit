import SwiftUI

struct WorkoutGraphView: View {
    let graph: WorkoutGraph?

    private var segments: [GraphSegment] { graph?.segments.filter { $0.durationSeconds > 0 } ?? [] }
    private var totalSeconds: Double { max(graph?.totalSeconds ?? segments.reduce(0) { $0 + $1.durationSeconds }, 1) }
    private var paces: [Double] { segments.compactMap(\.paceMinutesPerMile) }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                VStack(alignment: .leading, spacing: 3) {
                    Text("WORKOUT PREVIEW")
                        .font(.caption.weight(.bold))
                        .tracking(1.2)
                        .foregroundStyle(.white.opacity(0.62))
                    Text("Pace over time")
                        .font(.title2.weight(.bold))
                        .foregroundStyle(.white)
                }
                Spacer()
                HStack(spacing: 5) {
                    Circle()
                        .fill(segments.isEmpty ? Color.white.opacity(0.35) : AppTheme.lime)
                        .frame(width: 7, height: 7)
                    Text(segments.isEmpty ? "READY" : "GENERATED")
                }
                .font(.caption2.weight(.heavy))
                .tracking(0.7)
                .foregroundStyle(segments.isEmpty ? Color.white.opacity(0.58) : AppTheme.lime)
                .padding(.horizontal, 9)
                .padding(.vertical, 6)
                .background(Color.white.opacity(0.08), in: Capsule())
            }

            if segments.isEmpty || paces.isEmpty {
                ContentUnavailableView {
                    Label("Your graph appears here", systemImage: "chart.xyaxis.line")
                        .foregroundStyle(.white)
                } description: {
                    Text("Generate a workout to review it before approval.")
                        .foregroundStyle(.white.opacity(0.62))
                }
                .frame(maxWidth: .infinity, minHeight: 145)
            } else {
                HStack {
                    Text("Pace over time")
                    Spacer()
                    Text("Faster ↑")
                }
                .font(.caption.weight(.semibold))
                .foregroundStyle(.white.opacity(0.62))

                GeometryReader { proxy in
                    Canvas { context, size in
                        drawGrid(in: &context, size: size)
                        drawWorkout(in: &context, size: size)
                    }
                }
                .frame(height: 175)

                HStack {
                    Text("0:00")
                    Spacer()
                    Text(formatDuration(totalSeconds))
                }
                .font(.caption.monospacedDigit())
                .foregroundStyle(.white.opacity(0.62))
            }
        }
        .padding(18)
        .background {
            LinearGradient(
                colors: [AppTheme.forest, AppTheme.deepForest],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
        }
        .clipShape(RoundedRectangle(cornerRadius: 22, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 22, style: .continuous)
                .stroke(Color.white.opacity(0.08), lineWidth: 1)
        }
        .shadow(color: AppTheme.deepForest.opacity(0.16), radius: 14, y: 7)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(accessibilitySummary)
    }

    private func drawGrid(in context: inout GraphicsContext, size: CGSize) {
        let bounds = chartBounds(size)
        var path = Path()
        for index in 0...4 {
            let y = bounds.minY + bounds.height * CGFloat(index) / 4
            path.move(to: CGPoint(x: bounds.minX, y: y))
            path.addLine(to: CGPoint(x: bounds.maxX, y: y))
        }
        context.stroke(path, with: .color(.white.opacity(0.12)), lineWidth: 1)

        guard let fastest = paces.min(), let slowest = paces.max() else { return }
        for index in 0...4 {
            let pace = fastest + (slowest - fastest) * Double(index) / 4
            let point = CGPoint(x: 0, y: bounds.minY + bounds.height * CGFloat(index) / 4 - 7)
            context.draw(
                Text(formatPace(pace)).font(.caption2.monospacedDigit()).foregroundColor(.white.opacity(0.65)),
                at: point,
                anchor: .leading
            )
        }
    }

    private func drawWorkout(in context: inout GraphicsContext, size: CGSize) {
        let bounds = chartBounds(size)
        guard let minPace = paces.min(), let maxPace = paces.max() else { return }
        let spread = max(maxPace - minPace, 0.5)
        let adjustedMin = max(0.1, minPace - (spread == 0.5 ? 0.25 : 0))
        let adjustedMax = maxPace + (spread == 0.5 ? 0.25 : 0)
        let adjustedSpread = max(adjustedMax - adjustedMin, 0.5)

        var elapsed = 0.0
        for segment in segments {
            let startX = bounds.minX + bounds.width * CGFloat(elapsed / totalSeconds)
            elapsed += segment.durationSeconds
            let endX = bounds.minX + bounds.width * CGFloat(elapsed / totalSeconds)
            let pace = segment.paceMinutesPerMile ?? adjustedMax
            let y = bounds.minY + bounds.height * CGFloat((pace - adjustedMin) / adjustedSpread)
            let rect = CGRect(x: startX, y: y, width: max(endX - startX, 1.5), height: bounds.maxY - y)
            let color = color(for: segment.intensity)
            context.fill(Path(rect), with: .color(color.opacity(0.34)))

            var top = Path()
            top.move(to: CGPoint(x: startX, y: y))
            top.addLine(to: CGPoint(x: endX, y: y))
            context.stroke(top, with: .color(color), lineWidth: 3)
        }
    }

    private func chartBounds(_ size: CGSize) -> CGRect {
        CGRect(x: 42, y: 8, width: max(size.width - 42, 1), height: max(size.height - 14, 1))
    }

    private func color(for intensity: String?) -> Color {
        let value = (intensity ?? "").lowercased()
        if value.contains("rest") || value.contains("recovery") || value.contains("walk") { return .mint }
        if value.contains("warm") || value.contains("cool") { return .teal }
        return AppTheme.lime
    }

    private func formatPace(_ pace: Double) -> String {
        let seconds = max(0, Int((pace * 60).rounded()))
        return "\(seconds / 60):\(String(format: "%02d", seconds % 60))"
    }

    private func formatDuration(_ seconds: Double) -> String {
        let value = max(0, Int(seconds.rounded()))
        if value >= 3600 {
            return "\(value / 3600):\(String(format: "%02d", (value % 3600) / 60)):00"
        }
        return "\(value / 60):\(String(format: "%02d", value % 60))"
    }

    private var accessibilitySummary: String {
        guard !segments.isEmpty else { return "Workout graph placeholder" }
        return "Workout graph with \(segments.count) segments lasting \(formatDuration(totalSeconds))"
    }
}
