#!/bin/bash

# Name of the output file
OUTPUT_FILE="ecofac.txt"

# Clear or create the output file
> "$OUTPUT_FILE"

echo "Starting extraction... This may take a moment depending on your repo size."

# Find all files, excluding common non-code directories, dependencies, and binary files
find . -type f \
    -not -path "*/\.git/*" \
    -not -path "*/node_modules/*" \
    -not -path "*/venv/*" \
    -not -path "*/\.venv/*" \
    -not -path "*/__pycache__/*" \
    -not -path "*/dist/*" \
    -not -path "*/build/*" \
    -not -path "*/.next/*" \
    -not -path "*/.cache/*" \
    -not -path "*/.idea/*" \
    -not -path "*/.vscode/*" \
    -not -name "*.lock" \
    -not -name "package-lock.json" \
    -not -name "yarn.lock" \
    -not -name "poetry.lock" \
    -not -name "*.pyc" \
    -not -name "*.pyo" \
    -not -name "*.class" \
    -not -name "*.o" \
    -not -name "*.so" \
    -not -name "*.dll" \
    -not -name "*.exe" \
    -not -name "*.bin" \
    -not -name "*.dat" \
    -not -name "*.png" \
    -not -name "*.jpg" \
    -not -name "*.jpeg" \
    -not -name "*.gif" \
    -not -name "*.svg" \
    -not -name "*.ico" \
    -not -name "*.mp3" \
    -not -name "*.mp4" \
    -not -name "*.wav" \
    -not -name "*.zip" \
    -not -name "*.tar" \
    -not -name "*.gz" \
    -not -name "*.pdf" \
    -not -name "*.woff" \
    -not -name "*.woff2" \
    -not -name "*.ttf" \
    -not -name "*.eot" \
    | sort | while read -r file; do
    
    # Write a clear, AI-readable header for each file
    echo "======================================================================" >> "$OUTPUT_FILE"
    echo "FILE: $file" >> "$OUTPUT_FILE"
    echo "======================================================================" >> "$OUTPUT_FILE"
    
    # Append the file contents
    cat "$file" >> "$OUTPUT_FILE"
    
    # Add spacing between files for better readability
    echo -e "\n\n" >> "$OUTPUT_FILE"
done

echo "Extraction complete! All code has been saved to: $OUTPUT_FILE"
