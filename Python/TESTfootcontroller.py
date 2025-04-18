
#Issues outstanding, not standardizing clip length until end
#not basing it off of first clip everytime
from pythonosc import udp_client, dispatcher, osc_server
import time
import keyboard
import threading

# Global OSC dispatcher and server for receiving Ableton responses on port 11001.
global_dispatcher = dispatcher.Dispatcher()
global_osc_server = None
base_clip_length = None  # Holds the length (in beats) of the first recorded clip.
# Global toggling variables.
waiting_for_refire = False
current_active_track = None  # The track index of the clip currently recording.
all_clips_recorded = False   # Flag to indicate when all clips have been recorded

def start_global_osc_server():
    global global_osc_server
    ip = "127.0.0.1"
    port = 11001  # Fixed port for receiving from Ableton.
    global_osc_server = osc_server.ThreadingOSCUDPServer((ip, port), global_dispatcher)
    thread = threading.Thread(target=global_osc_server.serve_forever, daemon=True)
    thread.start()
    print(f"Global OSC server started on {ip}:{port}")
    return thread

# --- State Tracking ---
class StateTracker:
    def __init__(self):
        # Designated tracks: tracks 1, 3, 5, 7 (indexes 0, 2, 4, 6)
        self.track_has_clip = {0: False, 2: False, 4: False, 6: False}
        self.clip_slot_index = 0  # Always using the first clip slot.
        self.validation_interval = 7.0  # For background validation.
        self.validation_running = False
        self.validation_thread = None
        self.lock = threading.Lock()
    
    def mark_track_has_clip(self, track_index, has_clip=True):
        with self.lock:
            if track_index in self.track_has_clip:
                self.track_has_clip[track_index] = has_clip
                print(f"Internal state updated: Track {track_index+1} has clip: {has_clip}")
    
    def get_next_empty_track(self):
        with self.lock:
            for track_idx in [0, 2, 4, 6]:
                if not self.track_has_clip[track_idx]:
                    return track_idx
            return None
    
    def get_filled_tracks(self):
        with self.lock:
            return [idx for idx, has_clip in self.track_has_clip.items() if has_clip]
    
    def get_empty_tracks(self):
        with self.lock:
            return [idx for idx, has_clip in self.track_has_clip.items() if not has_clip]
    
    def are_all_tracks_filled(self):
        with self.lock:
            return all(self.track_has_clip.values())
    
    def start_background_validation(self, client):
        if self.validation_thread is not None and self.validation_thread.is_alive():
            print("Background validation already running")
            return
        self.validation_running = True
        self.validation_thread = threading.Thread(
            target=self._background_validation_loop,
            args=(client,),
            daemon=True
        )
        self.validation_thread.start()
        print(f"Background validation started (every {self.validation_interval} seconds)")
    
    def stop_background_validation(self):
        self.validation_running = False
        if self.validation_thread:
            self.validation_thread.join(timeout=1.0)
            print("Background validation stopped")
    
    def _background_validation_loop(self, client):
        print("Background validation thread started")
        while self.validation_running:
            for _ in range(int(self.validation_interval * 2)):
                if not self.validation_running:
                    break
                time.sleep(0.5)
            if not self.validation_running:
                break
            try:
                print("\n=== Background validation running ===")
                validate_state_with_ableton(client, self)
                print("=== Background validation complete ===\n")
            except Exception as e:
                print(f"Error in background validation: {e}")

# --- OSC Query Helpers ---
def query_clip_loop_points(client, track_index, clip_slot_index, timeout=6.0):
    """
    Queries Ableton for the loop start and end positions of a clip.
    Returns [loop_start, loop_end] (in beats) or [None, None] if not available.
    This function will wait (polling in a loop) until the timeout expires.
    """
    event = threading.Event()
    result = [None, None]

    def loop_start_handler(unused_addr, *args):
        # Debug: print raw args received.
        print(f"[DEBUG] Loop start handler received args: {args}")
        if len(args) >= 3:
            if int(args[0]) == track_index and int(args[1]) == clip_slot_index:
                result[0] = float(args[2])
                if result[1] is not None:
                    event.set()

    def loop_end_handler(unused_addr, *args):
        print(f"[DEBUG] Loop end handler received args: {args}")
        if len(args) >= 3:
            if int(args[0]) == track_index and int(args[1]) == clip_slot_index:
                result[1] = float(args[2])
                if result[0] is not None:
                    event.set()

    global_dispatcher.map("/live/clip/get/loop_start", loop_start_handler)
    global_dispatcher.map("/live/clip/get/loop_end", loop_end_handler)
    
    # Clear any previous messages in the queue
    time.sleep(0.02)
    
    # Send the queries with a small delay between them
    client.send_message("/live/clip/get/loop_start", [track_index, clip_slot_index])
    time.sleep(0.05)  # Added delay between messages to ensure they're processed separately
    client.send_message("/live/clip/get/loop_end", [track_index, clip_slot_index])
    
    start_time = time.time()
    while not event.is_set() and time.time() - start_time < timeout:
        time.sleep(0.1)
    
    global_dispatcher.unmap("/live/clip/get/loop_start", loop_start_handler)
    global_dispatcher.unmap("/live/clip/get/loop_end", loop_end_handler)
    
    print(f"[DEBUG] Final query result for track {track_index+1}, slot {clip_slot_index+1}: {result}")
    return result

def enforce_clip_loop_points(client, track_index, clip_slot_index, expected_start, expected_end, delay=0.5, timeout=2.0):
    """
    After a delay, queries back the loop points and, if they do not match the expected values,
    re-sends the set commands.
    """
    time.sleep(delay)
    loop_points = query_clip_loop_points(client, track_index, clip_slot_index, timeout)
    print(f"[DEBUG] Enforcement: Queried loop points for track {track_index+1}, slot {clip_slot_index+1}: {loop_points}")
    if loop_points[0] != expected_start or loop_points[1] != expected_end:
        print(f"[DEBUG] Loop points mismatch. Re-sending commands: start={expected_start}, end={expected_end}")
        
        # Clear any previous messages
        time.sleep(0.02)
        
        # Send commands with delay between them
        client.send_message("/live/clip/set/loop_start", [track_index, clip_slot_index, expected_start])
        time.sleep(0.05)
        client.send_message("/live/clip/set/loop_end", [track_index, clip_slot_index, expected_end])
        
        time.sleep(0.5)  # Wait for commands to be processed
        
        loop_points = query_clip_loop_points(client, track_index, clip_slot_index, timeout)
        print(f"[DEBUG] Loop points after enforcement: {loop_points}")
    else:
        print("[DEBUG] Loop points correctly set.")

# --- Initializing Base Clip Length ---
def initialize_base_clip_length(client, state_tracker):
    """
    Attempts to get the loop length from track 0, clip 0 and set it as the base clip length.
    """
    global base_clip_length
    
    if not state_tracker.track_has_clip[0]:
        print("[ERROR] Cannot initialize base clip length - track 1, slot 1 has no clip.")
        return False
    
    print("[DEBUG] Initializing base clip length from track 1, slot 1...")
    
    # Try multiple times if needed
    for attempt in range(3):
        # Give Ableton time to finish processing
        time.sleep(0.5)
        
        loop_points = query_clip_loop_points(client, 0, 0, timeout=2.0)
        if loop_points[0] is not None and loop_points[1] is not None:
            base_clip_length = loop_points[1] - loop_points[0]
            print(f"[DEBUG] Base clip length set to {base_clip_length} beats (attempt {attempt+1}).")
            return True
        else:
            print(f"[DEBUG] Failed to get loop points on attempt {attempt+1}, retrying...")
    
    print("[ERROR] Failed to initialize base clip length after multiple attempts.")
    return False

# --- Finalizing Recording ---
def finalize_recording(client, track_index, clip_slot_index, state_tracker):
    """
    After a recording has been stopped by re-firing the clip, this function waits and
    polls repeatedly for valid loop start and end points.
    • If the finalized clip is the first clip (track 1, slot 1) and base_clip_length is unset,
      it updates base_clip_length.
    • It checks if all tracks are now filled, and if so, triggers the synchronization.
    """
    global base_clip_length, all_clips_recorded
    print(f"[DEBUG] Finalizing recording on track {track_index+1}, slot {clip_slot_index+1}...")
    
    # Wait for the clip to be properly loaded after recording
    time.sleep(1.0)
    
    max_attempts = 20  # Poll up to 20 times (e.g., 10 seconds with a 0.5-second interval)
    attempt = 0
    loop_points = [None, None]
    while attempt < max_attempts:
        loop_points = query_clip_loop_points(client, track_index, clip_slot_index, timeout=1.0)
        if loop_points[0] is not None and loop_points[1] is not None:
            break
        attempt += 1
        time.sleep(0.5)
    
    print(f"[DEBUG] Final loop points for track {track_index+1}, slot {clip_slot_index+1}: {loop_points}")
    
    if loop_points[0] is not None and loop_points[1] is not None:
        clip_length = loop_points[1] - loop_points[0]
        
        # If this is the first clip, set the base clip length
        if base_clip_length is None and track_index == 0 and clip_slot_index == 0:
            base_clip_length = clip_length
            print(f"[DEBUG] Base clip length updated to {base_clip_length} beats (from first clip).")
        
        # Check if all designated tracks now have clips
        if state_tracker.are_all_tracks_filled():
            print("[DEBUG] All designated tracks now have clips. Starting synchronization after delay...")
            all_clips_recorded = True
            # Use a timer to allow a moment for final processing
            update_timer = threading.Timer(2.0, update_all_clips_loop_points, args=(client, state_tracker))
            update_timer.daemon = True
            update_timer.start()
    else:
        print("[DEBUG] Unable to capture loop point values after finalization.")

# --- Update All Clips Loop Points ---
def update_all_clips_loop_points(client, state_tracker):
    """
    Updates all recorded clips to have the same loop_end value based on the global base_clip_length.
    This is called automatically after all clips have been recorded or manually via keyboard shortcut.
    """
    global base_clip_length
    
    # If base_clip_length is not set, try to get it from track 0
    if base_clip_length is None:
        if not initialize_base_clip_length(client, state_tracker):
            print("[ERROR] Cannot update clips - failed to establish base length.")
            return
    
    filled_tracks = state_tracker.get_filled_tracks()
    print(f"Updating loop points for all clips to match length: {base_clip_length} beats")
    
    for track_idx in filled_tracks:
        print(f"Setting loop points for track {track_idx+1}, slot {state_tracker.clip_slot_index+1}")
        
        # Clear any previous messages
        time.sleep(0.05)
        
        # Send the set commands with a delay between them
        client.send_message("/live/clip/set/loop_start", [track_idx, state_tracker.clip_slot_index, 0.0])
        time.sleep(0.05)
        client.send_message("/live/clip/set/loop_end", [track_idx, state_tracker.clip_slot_index, base_clip_length])
        
        # Wait briefly before moving to the next track
        time.sleep(0.2)
    
    # After setting all clips, verify each one in separate threads
    for track_idx in filled_tracks:
        threading.Thread(
            target=enforce_clip_loop_points, 
            args=(client, track_idx, state_tracker.clip_slot_index, 0.0, base_clip_length),
            daemon=True
        ).start()
        # Stagger the verification threads
        time.sleep(0.1)
    
    print("[DEBUG] All clips updated to match base length.")

# --- Recording Function ---
def record_clip(client, track_index, clip_slot_index, state_tracker):
    """
    Fires a clip slot to record a new clip.
    This function disarms all tracks, arms the chosen track, and fires the clip slot.
    It does not finalize (query loop points) immediately.
    Instead, toggling behavior (via comma key) will control stopping and finalization.
    """
    print(f"Recording new clip on track {track_index+1}, slot {clip_slot_index+1}")
    for i in range(8):
        client.send_message("/live/track/set/arm", [i, 0])
    client.send_message("/live/track/set/arm", [track_index, 1])
    client.send_message("/live/clip_slot/fire", [track_index, clip_slot_index])
    state_tracker.mark_track_has_clip(track_index, True)
    print(f"Recording started on track {track_index+1}, slot {clip_slot_index+1}")
    return

# --- Connection and Validation Helpers ---
def verify_ableton_connection(client, timeout=1.0):
    print("Verifying connection to Ableton...")
    event = threading.Event()
    def handle_response(*args):
        print(f"Received response from Ableton: {args}")
        event.set()
    global_dispatcher.map("/live/song/get/tempo", handle_response)
    client.send_message("/live/song/get/tempo", [])
    client.send_message("/live/test", [])
    start_time = time.time()
    while not event.is_set() and time.time() - start_time < timeout:
        time.sleep(0.005)
    global_dispatcher.unmap("/live/song/get/tempo", handle_response)
    if event.is_set():
        print("Successfully connected to Ableton!")
        return True
    else:
        print("Could not verify connection to Ableton. Check that AbletonOSC is running.")
        return False

def validate_state_with_ableton(client, state_tracker):
    print("Validating internal clip state with Ableton...")
    for track_idx in [0, 2, 4, 6]:
        has_clip = check_track_has_clip(client, track_idx, state_tracker.clip_slot_index)
        state_tracker.mark_track_has_clip(track_idx, has_clip)
    filled_tracks = [t+1 for t in state_tracker.get_filled_tracks()]
    empty_tracks  = [t+1 for t in state_tracker.get_empty_tracks()]
    print(f"Current state - Tracks with clips: {filled_tracks if filled_tracks else 'none'}")
    print(f"Current state - Empty tracks: {empty_tracks if empty_tracks else 'none'}")

def check_track_has_clip(client, track_index, clip_slot_index):
    print(f"Checking if track {track_index+1}, slot {clip_slot_index+1} has a clip...")
    event = threading.Event()
    result = [None]
    def handler(unused_addr, *args):
        if len(args) >= 3:
            if int(args[0]) == track_index and int(args[1]) == clip_slot_index:
                result[0] = bool(int(args[2]))
                event.set()
                print(f"Response: Track {track_index+1}, slot {clip_slot_index+1} has clip: {result[0]}")
    global_dispatcher.map("/live/clip_slot/get/has_clip/return", handler)
    global_dispatcher.map("/live/clip_slot/get/has_clip", handler)
    global_dispatcher.map("/live/clip/get/exists/return", handler)
    client.send_message("/live/clip_slot/get/has_clip", [track_index, clip_slot_index])
    time.sleep(0.005)  # Increased delay to ensure message is sent
    client.send_message("/live/clip/get/exists", [track_index, clip_slot_index])
    start_time = time.time()
    while not event.is_set() and time.time() - start_time < 0.5:  # Increased timeout
        time.sleep(0.01)  # Increased polling interval
    global_dispatcher.unmap("/live/clip_slot/get/has_clip/return", handler)
    global_dispatcher.unmap("/live/clip_slot/get/has_clip", handler)
    global_dispatcher.unmap("/live/clip/get/exists/return", handler)
    if result[0] is None:
        print(f"No response received for track {track_index+1}; assuming no clip.")
        return False
    return result[0]

# --- Main and Keyboard Handling ---
def main():
    global waiting_for_refire, current_active_track, all_clips_recorded
    start_global_osc_server()
    state_tracker = StateTracker()
    ip = "127.0.0.1"   # AbletonOSC sending address
    port = 11000       # AbletonOSC sending port
    client = udp_client.SimpleUDPClient(ip, port)
    
    print(f"Attempting to connect to AbletonOSC server at {ip}:{port}")
    if not verify_ableton_connection(client):
        input("Press Enter to exit...")
        return
    
    validate_state_with_ableton(client, state_tracker)
    state_tracker.start_background_validation(client)
    
    print("Foot controller started.")
    print("- Press ',' to toggle recording/refiring")
    print("- Press '.' to stop all clips (playback only)")
    print("- Press '/' to force state validation with Ableton")
    print("- Press 's' to synchronize all clips to the same length")
    print("- Press 'up' and 'down' for other controls (not used here)")
    print("- Press 'esc' to exit")
    
    is_processing = False  # Local to main
    
    def handle_comma_press(e):
        nonlocal is_processing
        global waiting_for_refire, current_active_track, all_clips_recorded
        with threading.Lock():
            if is_processing:
                print("Already processing a command. Please wait...")
                return
            is_processing = True
            try:
                if waiting_for_refire:
                    # Second press: re-fire current clip to stop recording.
                    print(f"Refiring clip in track {current_active_track+1}, slot {state_tracker.clip_slot_index+1} to stop recording.")
                    client.send_message("/live/clip_slot/fire", [current_active_track, state_tracker.clip_slot_index])
                    # Finalize the recording in a background thread.
                    threading.Thread(
                        target=finalize_recording, 
                        args=(client, current_active_track, state_tracker.clip_slot_index, state_tracker), 
                        daemon=True
                    ).start()
                    waiting_for_refire = False
                else:
                    # Reset sync flag when starting a new recording session
                    if all_clips_recorded:
                        all_clips_recorded = False
                    
                    # First or third press: record a new clip.
                    track_to_use = state_tracker.get_next_empty_track()
                    if track_to_use is None:
                        print("All designated tracks (1, 3, 5, 7) are full! Clear some clips before recording more.")
                        return
                    print(f"Recording new clip in track {track_to_use+1}, slot {state_tracker.clip_slot_index+1}")
                    record_clip(client, track_to_use, state_tracker.clip_slot_index, state_tracker)
                    current_active_track = track_to_use
                    waiting_for_refire = True
            finally:
                is_processing = False
    
    def stop_clips(e):
        print("Stopping all clips (playback only)...")
        client.send_message("/live/song/stop_all_clips", [])
    
    def force_validation(e):
        nonlocal is_processing
        with threading.Lock():
            if is_processing:
                print("Already processing a command. Please wait...")
                return
            is_processing = True
            try:
                print("Forcing state validation with Ableton...")
                validate_state_with_ableton(client, state_tracker)
            finally:
                is_processing = False
    
    def sync_all_clips(e):
        nonlocal is_processing
        with threading.Lock():
            if is_processing:
                print("Already processing a command. Please wait...")
                return
            is_processing = True
            try:
                print("Manually triggering clip synchronization...")
                update_all_clips_loop_points(client, state_tracker)
            finally:
                is_processing = False
    
    def handle_up_press(e):
        print("Up key pressed (no loop action)")
    
    def handle_down_press(e):
        print("Down key pressed (no loop action)")
    
    keyboard.on_press_key(',', handle_comma_press)
    keyboard.on_press_key('.', stop_clips)
    keyboard.on_press_key('/', force_validation)
    keyboard.on_press_key('s', sync_all_clips)  # Manual synchronization shortcut
    keyboard.on_press_key('up', handle_up_press)
    keyboard.on_press_key('down', handle_down_press)
    
    keyboard.wait('esc')
    print("Exiting foot controller...")
    state_tracker.stop_background_validation()
    if global_osc_server:
        global_osc_server.shutdown()

if __name__ == "__main__":
    main()