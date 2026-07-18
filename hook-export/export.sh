#!/bin/bash

# Exit on error
set -e

# Define directories
HOOKS_DIR="hooks"
MOMENTS_DIR="moments"
EXPORT_DIR="export"
TEMP_DIR=".temp_export"

# Find the moment video
MOMENT_FILE=$(find "$MOMENTS_DIR" -maxdepth 1 -name "*.mp4" | head -n 1)

if [ -z "$MOMENT_FILE" ]; then
    echo "Error: No moment video found in '$MOMENTS_DIR'!"
    exit 1
fi

echo "Found moment video: $MOMENT_FILE"

# Create directories
mkdir -p "$EXPORT_DIR"
mkdir -p "$TEMP_DIR"

# Clean up temp directory on exit
cleanup() {
    rm -rf "$TEMP_DIR"
}
trap cleanup EXIT

# 1. Format the moment video to HEVC, 1080x1920, 30fps, 44.1kHz AAC stereo, and hvc1 tag
MOMENT_FORMATTED="$TEMP_DIR/moment_formatted.mp4"
echo "Formatting moment video for compatibility (HEVC hvc1, 1080x1920, 30fps)..."

if ! ffmpeg -y -i "$MOMENT_FILE" \
    -vf "scale=1080:1920,fps=30" \
    -c:v libx265 -vtag hvc1 \
    -c:a aac -ar 44100 -ac 2 \
    "$MOMENT_FORMATTED" > "$TEMP_DIR/ffmpeg_format.log" 2>&1; then
    echo "Error formatting moment video:"
    cat "$TEMP_DIR/ffmpeg_format.log"
    exit 1
fi

# 2. Get list of hooks
HOOK_FILES=("$HOOKS_DIR"/*.mp4)
TOTAL=${#HOOK_FILES[@]}

if [ "$TOTAL" -eq 0 ] || [ ! -e "${HOOK_FILES[0]}" ]; then
    echo "Error: No hook videos found in '$HOOKS_DIR'!"
    exit 1
fi

echo "Found $TOTAL hooks to process."

# 3. Concatenate each hook with the formatted moment video
IDX=1
for hook in "${HOOK_FILES[@]}"; do
    filename=$(basename "$hook")
    output="$EXPORT_DIR/$filename"
    echo "[$IDX/$TOTAL] Plugging '$filename' to moment video..."
    
    if ! ffmpeg -y -i "$hook" -i "$MOMENT_FORMATTED" \
        -filter_complex "[0:v][0:a][1:v][1:a] concat=n=2:v=1:a=1 [v][a]" \
        -map "[v]" -map "[a]" \
        -c:v libx265 -vtag hvc1 \
        -c:a aac -ar 44100 -ac 2 \
        "$output" > "$TEMP_DIR/ffmpeg_concat_${IDX}.log" 2>&1; then
        echo "Error concatenating '$filename':"
        cat "$TEMP_DIR/ffmpeg_concat_${IDX}.log"
        exit 1
    fi
        
    echo "Successfully exported '$filename'"
    IDX=$((IDX+1))
done

echo "All videos successfully exported to '$EXPORT_DIR/'!"
