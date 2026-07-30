import SwiftUI

struct UploadConfirmationView: View {
    @Environment(\.dismiss) private var dismiss
    let report: GarminUploadReport
    let connection: GarminStatus

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    HStack(alignment: .top, spacing: 14) {
                        Image(systemName: report.failed == 0 ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                            .font(.system(size: 42))
                            .foregroundStyle(report.failed == 0 ? AppTheme.success : .orange)
                        VStack(alignment: .leading, spacing: 5) {
                            Text(report.failed == 0
                                 ? "Upload confirmed — \(report.successful) of \(report.attempted)"
                                 : "Upload finished — \(report.successful) of \(report.attempted)")
                                .font(.title2.weight(.bold))
                            Text(report.message)
                                .foregroundStyle(.secondary)
                        }
                    }

                    VStack(alignment: .leading, spacing: 6) {
                        Label(connection.connected ? "Garmin connection verified" : "Garmin connection unavailable",
                              systemImage: connection.connected ? "link.circle.fill" : "link.badge.plus")
                            .font(.headline)
                        if let name = connection.accountName, !name.isEmpty {
                            Text("Connected as \(name)")
                        }
                        if let checked = connection.checkedAt, !checked.isEmpty {
                            Text("Checked \(checked)")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                    .padding(16)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(AppTheme.success.opacity(0.09))
                    .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))

                    ForEach(report.results) { result in
                        HStack(alignment: .top, spacing: 12) {
                            Image(systemName: result.ok ? "checkmark" : "xmark")
                                .font(.headline)
                                .foregroundStyle(result.ok ? AppTheme.success : .red)
                            VStack(alignment: .leading, spacing: 4) {
                                Text(result.source)
                                    .font(.headline)
                                if let workoutID = result.workoutID {
                                    Text("Added to your workout library · Garmin workout ID \(workoutID.description)")
                                        .font(.subheadline)
                                        .foregroundStyle(.secondary)
                                } else if let error = result.error {
                                    Text(error)
                                        .font(.subheadline)
                                        .foregroundStyle(.secondary)
                                }
                            }
                        }
                        .padding(.vertical, 5)
                    }
                }
                .padding()
            }
            .navigationTitle("Garmin delivery")
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }
}
