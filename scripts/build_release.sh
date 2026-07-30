#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
version="$(tr -d '[:space:]' < "$project_root/VERSION")"
release_name="PromptFit-Studio-${version}"
release_dir="$project_root/dist"
release_temp="$(mktemp -d)"
package_root="$release_temp/$release_name"
archive_path="$release_dir/$release_name.zip"

cleanup() {
  rm -rf -- "$release_temp"
}
trap cleanup EXIT

mkdir -p "$package_root" "$release_dir"
mkdir -p "$package_root/scripts"

cp \
  "$project_root/VERSION" \
  "$project_root/README.md" \
  "$project_root/QUICK_START.md" \
  "$project_root/RELEASE_NOTES.md" \
  "$project_root/PACE_TERMINOLOGY.md" \
  "$project_root/.dockerignore" \
  "$project_root/Dockerfile" \
  "$project_root/requirements.txt" \
  "$project_root/run_webapp.sh" \
  "$project_root/run_webapp.command" \
  "$project_root/run_webapp_windows.bat" \
  "$project_root/final_spec_compliant_fix.py" \
  "$project_root/hm_plan_calendar.py" \
  "$project_root/hm_plan_to_garmin.py" \
  "$project_root/ics_to_fit_gui.py" \
  "$project_root/training_plan_gui.py" \
  "$package_root/"

cp "$project_root/scripts/build_release.sh" "$package_root/scripts/"

cp -R \
  "$project_root/Running_Plans" \
  "$project_root/PromptFitIOS" \
  "$project_root/launchd" \
  "$project_root/webapp" \
  "$package_root/"

find "$package_root" -name '__pycache__' -type d -prune -exec rm -rf -- {} +
find "$package_root" -name '.DS_Store' -type f -delete
find "$package_root" -name '*.swp' -type f -delete
find "$package_root" -name 'xcuserdata' -type d -prune -exec rm -rf -- {} +
find "$package_root" -name 'DerivedData' -type d -prune -exec rm -rf -- {} +
rm -rf -- "$package_root/PromptFitIOS/DesignArchive"

(
  cd "$release_temp"
  zip -q -r "$archive_path" "$release_name"
)

printf 'Created %s\n' "$archive_path"
