import sys
import os
import time
import struct
import argparse
from pathlib import Path

# Resolve project root dynamically (2 levels up from guitaroids/audio)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(PROJECT_ROOT))

import numpy as np
import pygame
from guitaroids.songlib import load_tab

def get_onset(note):
    """Extract onset timing in seconds from note object."""
    for attr in ("onset_sec", "sec", "time", "start_sec", "onset"):
        if hasattr(note, attr):
            return getattr(note, attr)
    return 0.0

def get_pitch(note):
    """Extract MIDI pitch value from note object."""
    for attr in ("midi_pitch", "pitch", "midi_note", "value"):
        if hasattr(note, attr):
            return getattr(note, attr)
    return 60

def pitch_shift(audio_data, sample_rate, root_pitch, target_pitch):
    """Resample audio buffer to match target MIDI note pitch."""
    semitones = target_pitch - root_pitch
    pitch_factor = 2.0 ** (semitones / 12.0)
    
    new_length = int(len(audio_data) / pitch_factor)
    if new_length <= 0:
        return None
        
    indices = np.linspace(0, len(audio_data) - 1, new_length)
    resampled = np.interp(indices, np.arange(len(audio_data)), audio_data)
    
    max_val = np.max(np.abs(resampled))
    if max_val > 0:
        resampled = resampled / max_val
    pcm = (resampled * 32767 * 0.7).astype(np.int16)
    stereo = np.column_stack((pcm, pcm))
    return pygame.sndarray.make_sound(stereo)

def parse_sf2_samples_direct(sf2_path):
    """Extract sample metadata and raw PCM audio without audioop / sf2utils."""
    print(f"Parsing SoundFont RIFF header: {sf2_path.name}...")
    
    with open(sf2_path, "rb") as f:
        data = f.read()

    # Verify RIFF header
    if data[:4] != b"RIFF" or data[8:12] != b"sfbk":
        raise ValueError("Invalid SoundFont file format.")

    # Locate 'smpl' sub-chunk inside 'sdta' LIST
    smpl_pos = data.find(b"smpl")
    if smpl_pos == -1:
        raise ValueError("Could not find sample audio chunk in SoundFont.")

    smpl_size = struct.unpack("<I", data[smpl_pos + 4 : smpl_pos + 8])[0]
    pcm_start = smpl_pos + 8
    raw_pcm = data[pcm_start : pcm_start + smpl_size]

    # Convert 16-bit PCM bytes to float32 audio array
    audio_data = np.frombuffer(raw_pcm, dtype=np.int16).astype(np.float32) / 32768.0

    # Read shdr (sample headers) inside pdta chunk to find individual instrument slices
    shdr_pos = data.find(b"shdr")
    samples_info = []

    if shdr_pos != -1:
        shdr_size = struct.unpack("<I", data[shdr_pos + 4 : shdr_pos + 8])[0]
        header_block = data[shdr_pos + 8 : shdr_pos + 8 + shdr_size]
        
        # Each shdr record is 46 bytes long
        record_count = len(header_block) // 46
        for i in range(record_count - 1):  # Skip EOS (End of Samples)
            offset = i * 46
            rec = header_block[offset : offset + 46]
            name = rec[:20].decode("ascii", errors="ignore").rstrip("\x00")
            start_sample, end_sample = struct.unpack("<II", rec[20:28])
            sample_rate, original_pitch = struct.unpack("<IB", rec[36:41])
            
            if end_sample > start_sample and (end_sample - start_sample) > 500:
                samples_info.append({
                    "name": name,
                    "start": start_sample,
                    "end": end_sample,
                    "sample_rate": sample_rate,
                    "root_pitch": original_pitch if original_pitch > 0 else 60
                })

    return audio_data, samples_info

def load_guitar_sample(sf2_path, search_term="HollowBodyR"):
    """Load a specific instrument sample from the SoundFont."""
    audio_data, samples_info = parse_sf2_samples_direct(sf2_path)
    
    selected_sample = None
    if samples_info:
        print("\n=== Detected Instrument Samples ===")
        for s in samples_info:
            print(f"- {s['name']} (Root Pitch: {s['root_pitch']}, Rate: {s['sample_rate']} Hz)")
            
        # 1. Search for matching sample name
        for s in samples_info:
            if search_term.lower() in s["name"].lower():
                selected_sample = s
                break
                
        # 2. Fallback to first available sample
        if not selected_sample:
            selected_sample = samples_info[0]

    if selected_sample:
        print(f"\nLoaded target instrument slice: '{selected_sample['name']}'")
        sample_wave = audio_data[selected_sample["start"] : selected_sample["end"]]
        sample_rate = selected_sample["sample_rate"]
        root_pitch = selected_sample["root_pitch"]
    else:
        print("\nFallback: Using default full soundbank offset")
        sample_wave = audio_data[:44100]
        sample_rate = 44100
        root_pitch = 60

    return sample_wave, sample_rate, root_pitch

def play(sample_search="HollowBodyR"):
    sf2_path = PROJECT_ROOT / "assets" / "Guitarramelodica.sf2"
    gp4_path = PROJECT_ROOT / "songs" / "gnr_song.gp4"

    if not sf2_path.exists():
        print(f"Error: Could not find SoundFont at {sf2_path}")
        return

    # 1. Initialize Pygame Audio Engine
    pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)
    pygame.mixer.set_num_channels(32)

    # 2. Parse Song Tab
    entry = load_tab(gp4_path)
    if not entry or not entry.chart:
        print("Error: Could not parse song chart.")
        return

    notes = sorted(entry.chart.notes, key=get_onset)
    title = getattr(entry, "title", getattr(entry.chart, "title", "Sweet Child O' Mine"))
    print(f"Loaded '{title}' ({len(notes)} notes).")

    # 3. Extract sample waveform directly from Guitarramelodica.sf2
    base_wave, sample_rate, root_pitch = load_guitar_sample(sf2_path, search_term=sample_search)

    # 4. Pre-render pitch samples
    print("\nPre-rendering pitch-shifted note samples...")
    sound_cache = {}
    for note in notes:
        pitch = get_pitch(note)
        if pitch not in sound_cache:
            snd = pitch_shift(base_wave, sample_rate, root_pitch, pitch)
            if snd:
                sound_cache[pitch] = snd

    # 5. Play directly through Mac speakers
    print(f"\n---> PLAYING '{title}' WITH GUITARRAMELODICA SOUNDFONT <---")
    print("Press Ctrl+C to stop.\n")
    start_time = time.time()

    try:
        for note in notes:
            onset = get_onset(note)
            wait = onset - (time.time() - start_time)
            if wait > 0:
                time.sleep(wait)

            pitch = get_pitch(note)
            if pitch in sound_cache:
                sound_cache[pitch].play()

        time.sleep(1.2)
        print("Playback complete!")

    except KeyboardInterrupt:
        print("\nPlayback stopped by user.")
    finally:
        pygame.mixer.quit()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Play GP4 tab with custom SoundFont sample.")
    parser.add_argument("sample", nargs="?", default="HollowBodyR", help="Sample name keyword (e.g. HollowBodyR, RockGuitarR)")
    args = parser.parse_args()
    
    play(sample_search=args.sample)