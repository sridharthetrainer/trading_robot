#!/usr/bin/env bash
set -euo pipefail

cd /home/sridhar/Desktop/trading_robot

VERSION="20260621_064223"
ZIP_FILE="releases/trading_robot_code_${VERSION}.zip"
COMMIT_MSG="version: autonomous training and option bot audit backup ${VERSION}"

if [ ! -f "$ZIP_FILE" ]; then
  echo "Missing $ZIP_FILE"
  exit 1
fi

echo "Version archive:"
ls -lh "$ZIP_FILE"
sha256sum "$ZIP_FILE"

echo
echo "Committing current project version..."
git add -A
if git diff --cached --quiet; then
  echo "No staged changes to commit."
else
  git commit -m "$COMMIT_MSG"
fi

echo
echo "Pushing to GitHub..."
git push origin main

echo
echo "Uploading zip to Google Drive..."
rclone copy "$ZIP_FILE" gdrive:trading_robot/releases --transfers 1 --checkers 4 --progress

echo
echo "Verifying Google Drive upload..."
rclone ls gdrive:trading_robot/releases | grep "trading_robot_code_${VERSION}.zip"

echo
echo "Done: GitHub pushed and Google Drive zip uploaded."
