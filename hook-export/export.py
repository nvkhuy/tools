#!/usr/bin/env python3
import os
import sys
import time
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

HOOKS_DIR = Path("hooks")
MOMENTS_DIR = Path("moments")
EXPORT_DIR = Path("export")
TEMP_DIR = Path(".temp_export")

def get_moment_file():
    if not MOMENTS_DIR.exists():
        return None
    for f in MOMENTS_DIR.iterdir():
        if f.is_file() and f.suffix.lower() == ".mp4":
            return f
    return None

def get_hook_files():
    if not HOOKS_DIR.exists():
        return []
    hooks = []
    for f in HOOKS_DIR.iterdir():
        if f.is_file() and f.suffix.lower() == ".mp4" and not f.name.startswith("."):
            hooks.append(f)
    # Sort hooks for deterministic processing order
    hooks.sort(key=lambda x: x.name)
    return hooks

def check_has_audio(file_path):
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "a",
        "-show_entries", "stream=codec_type",
        "-of", "csv=p=0",
        str(file_path)
    ]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        return len(result.stdout.strip()) > 0
    except subprocess.CalledProcessError:
        return False

def get_duration(file_path):
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "csv=p=0",
        str(file_path)
    ]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        return float(result.stdout.strip())
    except (subprocess.CalledProcessError, ValueError):
        return 0.0

def check_videotoolbox_support():
    cmd = ["ffmpeg", "-encoders"]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        return "hevc_videotoolbox" in result.stdout
    except subprocess.CalledProcessError:
        return False

def format_moment(moment_file, formatted_path, encoder):
    print(f"Formatting moment video for compatibility (HEVC hvc1, 1080x1920, 30fps)...")
    has_audio = check_has_audio(moment_file)
    
    if has_audio:
        filter_complex = "[0:v]scale=1080:1920,fps=30,setsar=1[v]; [0:a]aformat=sample_rates=44100:channel_layouts=stereo[a]"
        cmd = [
            "ffmpeg", "-y", "-i", str(moment_file),
            "-filter_complex", filter_complex,
            "-map", "[v]", "-map", "[a]",
            "-c:v", encoder, "-vtag", "hvc1", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "44100", "-ac", "2",
            str(formatted_path)
        ]
    else:
        filter_complex = "[0:v]scale=1080:1920,fps=30,setsar=1[v]; anullsrc=channel_layout=stereo:sample_rate=44100[a]"
        cmd = [
            "ffmpeg", "-y", "-i", str(moment_file),
            "-filter_complex", filter_complex,
            "-map", "[v]", "-map", "[a]", "-shortest",
            "-c:v", encoder, "-vtag", "hvc1", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "44100", "-ac", "2",
            str(formatted_path)
        ]
        
    log_file = TEMP_DIR / "moment_format.log"
    with open(log_file, "w") as f:
        result = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT)
    
    if result.returncode != 0:
        with open(log_file, "r") as f:
            log_content = f.read()
        raise RuntimeError(f"Failed to format moment video:\n{log_content}")

def process_single_hook(hook_file, moment_formatted, output_dir, encoder, threads=None):
    start_time = time.time()
    filename = hook_file.name
    output_path = output_dir / filename
    
    has_audio = check_has_audio(hook_file)
    duration = get_duration(hook_file)
    
    if has_audio:
        filter_complex = (
            "[0:v]scale=1080:1920,fps=30,setsar=1[v0]; "
            "[0:a]aformat=sample_rates=44100:channel_layouts=stereo[a0]; "
            "[v0][a0][1:v][1:a] concat=n=2:v=1:a=1 [v][a]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", str(hook_file),
            "-i", str(moment_formatted),
            "-filter_complex", filter_complex,
            "-map", "[v]", "-map", "[a]",
            "-c:v", encoder, "-vtag", "hvc1", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "44100", "-ac", "2"
        ]
    else:
        filter_complex = (
            f"anullsrc=channel_layout=stereo:sample_rate=44100:d={duration}[a0]; "
            "[0:v]scale=1080:1920,fps=30,setsar=1[v0]; "
            "[v0][a0][1:v][1:a] concat=n=2:v=1:a=1 [v][a]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", str(hook_file),
            "-i", str(moment_formatted),
            "-filter_complex", filter_complex,
            "-map", "[v]", "-map", "[a]",
            "-c:v", encoder, "-vtag", "hvc1", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "44100", "-ac", "2"
        ]
        
    if encoder == "libx265" and threads is not None:
        cmd.extend(["-threads", str(threads)])
        
    cmd.append(str(output_path))
        
    log_file = TEMP_DIR / f"ffmpeg_concat_{hook_file.stem}.log"
    with open(log_file, "w") as f:
        result = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT)
        
    elapsed = time.time() - start_time
    if result.returncode != 0:
        with open(log_file, "r") as f:
            log_content = f.read()
        return False, filename, elapsed, log_content
    return True, filename, elapsed, ""

def main():
    start_time = time.time()
    
    # 1. Validation
    moment_file = get_moment_file()
    if not moment_file:
        print(f"Error: No moment video found in '{MOMENTS_DIR}'!")
        sys.exit(1)
    print(f"Found moment video: {moment_file}")
    
    hook_files = get_hook_files()
    total_hooks = len(hook_files)
    if total_hooks == 0:
        print(f"Error: No hook videos found in '{HOOKS_DIR}'!")
        sys.exit(1)
    print(f"Found {total_hooks} hooks to process.")
    
    # 2. Setup dirs
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    
    # 3. Detect hardware acceleration
    use_hw = check_videotoolbox_support()
    encoder = "hevc_videotoolbox" if use_hw else "libx265"
    if use_hw:
        print("Hardware-accelerated HEVC encoder detected: hevc_videotoolbox")
    else:
        print("Using software HEVC encoder: libx265")
        
    # 4. Format moment video
    moment_formatted = TEMP_DIR / "moment_formatted.mp4"
    try:
        format_moment(moment_file, moment_formatted, encoder)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
        
    # 5. Process hooks in parallel
    cpu_count = os.cpu_count() or 4
    max_workers = min(4, cpu_count)
    
    # Allow overriding max workers via environment variable
    env_workers = os.environ.get("NUM_WORKERS")
    if env_workers:
        try:
            max_workers = int(env_workers)
        except ValueError:
            pass
            
    threads_per_process = None
    if encoder == "libx265":
        threads_per_process = max(1, cpu_count // max_workers)
        print(f"Processing hooks in parallel with {max_workers} workers (each using {threads_per_process} threads)...")
    else:
        print(f"Processing hooks in parallel with {max_workers} hardware-accelerated workers...")
    
    success_count = 0
    failures = []
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(process_single_hook, hook, moment_formatted, EXPORT_DIR, encoder, threads_per_process): hook
            for hook in hook_files
        }
        
        for idx, future in enumerate(as_completed(futures), 1):
            success, filename, elapsed, error_log = future.result()
            if success:
                success_count += 1
                print(f"[{idx}/{total_hooks}] Successfully exported '{filename}' (took {elapsed:.1f}s)")
            else:
                failures.append((filename, error_log))
                print(f"[{idx}/{total_hooks}] Failed to export '{filename}' (took {elapsed:.1f}s)")
                
    # 6. Cleanup
    for log in TEMP_DIR.glob("*.log"):
        try:
            log.unlink()
        except OSError:
            pass
    try:
        moment_formatted.unlink()
        TEMP_DIR.rmdir()
    except OSError:
        pass
        
    # 7. Report results
    total_elapsed = time.time() - start_time
    print(f"\nFinished in {total_elapsed:.1f}s.")
    print(f"Successfully exported {success_count}/{total_hooks} videos.")
    
    if failures:
        print(f"\n--- FAILURES ({len(failures)}) ---")
        for filename, log_content in failures:
            print(f"\nFailure details for '{filename}':")
            lines = log_content.splitlines()[-20:]
            print("\n".join(lines))
        sys.exit(1)
    else:
        print(f"All videos successfully exported to '{EXPORT_DIR}/'!")

if __name__ == "__main__":
    main()
