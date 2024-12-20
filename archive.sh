#!/bin/bash

# Get the tag name from the environment variable
REPO_TAG_NAME=$1

# Define the output zip file name
ZIP_FILE="8BP-$REPO_TAG_NAME.zip"

# Find all files in the current directory except shell scripta and the git related directories, and create a zip file
zip -r "$ZIP_FILE" . -x "*.sh" -x ".git/*" -x ".github/*" -x "switch/tinfoil/icons.db" -x "README.md"

echo "Files have been zipped into $ZIP_FILE, excluding all shell scripts and the git related directories."
