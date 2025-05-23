####Issues outstanding#####
# there is redundancy in the state_tracker that is worth optimizing
# takes a while for the track to start up
# if the delete from VR is implemented for single tracks, make sure you can't delete the first clip from player 1 bc it's the base clip
 
from pythonosc import udp_client, dispatcher, osc_server
import time
import keyboard
import threading

# Global OSC dispatcher and server for receiving Ableton responses on port 11001 and from client 2 on client2_port
global_dispatcher = dispatcher.Dispatcher()
global_osc_server = None
client2_dispatcher = dispatcher.Dispatcher()
client2_osc_server = None

base_clip_length = None  # Holds the length (in beats) of the first recorded clip.
# Global toggling variables.
waiting_for_refire_player1 = False
waiting_for_refire_player2 = False
current_active_track_player1 = None
current_active_track_player2 = None
all_clips_recorded = False   # Flag to indicate when all clips have been recorded

# Global flag to control the running state
running = True

# Add separate processing flags for each player
is_processing_player1 = False
is_processing_player2 = False

# Add thread locks for each player
player1_lock = threading.Lock()
player2_lock = threading.Lock()

client = None  # global client variable
client2_clients = []  # List of UDP clients for VR headsets

# Helper function to send to all client2 addresses
def send_to_all_client2_clients(clients, address, args):
    for c in clients:
        c.send_message(address, args)

def fire_scene(client, client2_clients, state_tracker, e=None):
    print("fire message sent for scene (only firing unarmed tracks)")
    # Send to VR headsets via PC Transmitter port (9001)
    for c in client2_clients:
        c.send_message("/pcplayall", [True])
    for i in range(8):
        if not state_tracker.get_track_is_armed(i):
            client.send_message("/live/clip_slot/fire", [i, 0])

def stop_clips(client, client2_clients, e=None):
    print("Stopping all clips (playback only)...")
    # Send to VR headsets via PC Transmitter port (9001)
    for c in client2_clients:
        c.send_message("/pcplayall", [False])
    client.send_message("/live/song/stop_all_clips", [])

def delete_scene(client, client2_clients, state_tracker, e=None):
    global base_clip_length, current_active_track_player1, current_active_track_player2
    global waiting_for_refire_player1, waiting_for_refire_player2
    print("fire message sent for scene")
    for c in client2_clients:
        c.send_message("/deleteall", [True])
    for i in range(8):
        client.send_message("/live/clip_slot/delete_clip", [i, 0])
    # Reset all state
    base_clip_length = None
    current_active_track_player1 = None
    current_active_track_player2 = None
    waiting_for_refire_player1 = False
    waiting_for_refire_player2 = False
    state_tracker.reset()

def handle_toggletrack(client, addr, *args):
    # args should contain [player, track, state]
    if len(args) < 3:
        print(f"Malformed message: {args}")
        return
    
    player = int(args[0])
    track = int(args[1])
    state = args[2]  # boolean value
    

    # Map to Ableton track index
    if player == 1:
        track_id = (track - 1) * 2  # 1→0, 2→2, 3→4, 4→6
    elif player == 2:
        track_id = (track - 1) * 2 + 1  # 1→1, 2→3, 3→5, 4→7
    else:
        print(f"Unknown player: {player}")
        return

    print(f"Player {player}, Track {track} (Ableton track {track_id}), State: {state}")

    if state:
        # Fire (play) the clip in slot 0
        client.send_message("/live/clip_slot/fire", [track_id, 0])
        # for c in client2_clients:
        #     c.send_message("/clipisplaying", [player, track-1, state])
    else:
        # Stop the clip in slot 0
        client.send_message("/live/clip/stop", [track_id, 0])
        # for c in client2_clients:
        #     c.send_message("/clipisplaying", [player, track-1, state])
                            
def start_global_osc_server():
    global global_osc_server
    ip = "127.0.0.1"
    port = 11001  # Fixed port for receiving from Ableton.
    global_osc_server = osc_server.ThreadingOSCUDPServer((ip, port), global_dispatcher)
    thread = threading.Thread(target=global_osc_server.serve_forever, daemon=True)
    thread.start()
    print(f"Global OSC server started on {ip}:{port}")
    return thread


def start_client2_osc_server(client, client2_clients, state_tracker):
    global client2_osc_server, client2_dispatcher
    client2_ip = "0.0.0.0"
    client2_port = 12000  # PC Receiver port to receive messages from headsets

    # Only create if not already running
    if client2_osc_server is not None:
        print(f"Client2 OSC server already running on {client2_ip}:{client2_port}")
        return

    client2_dispatcher = dispatcher.Dispatcher()
    # Handle messages from VR headsets
    client2_dispatcher.map("/playall", lambda addr, *args: fire_scene(client, client2_clients, state_tracker) if args[0] else stop_clips(client, client2_clients, None))
    client2_dispatcher.map("/deleteall", lambda addr, *args: delete_scene(client, client2_clients, state_tracker, None))
    client2_dispatcher.map("/toggletrack", lambda addr, *args: handle_toggletrack(client, addr, *args))

    client2_osc_server = osc_server.ThreadingOSCUDPServer((client2_ip, client2_port), client2_dispatcher)
    thread = threading.Thread(target=client2_osc_server.serve_forever, daemon=True)
    thread.start()
    print(f"Client2 OSC server started on {client2_ip}:{client2_port}")
    return thread

# --- State Tracking ---
class StateTracker:
    def __init__(self):
        # Designated tracks: Player 1 (1, 3, 5, 7) and Player 2 (2, 4, 6, 8)
        self.track_has_clip = {0: False, 1: False, 2: False, 3: False, 4: False, 5: False, 6: False, 7: False}
        self.track_is_armed = {0: False, 1: False, 2: False, 3: False, 4: False, 5: False, 6: False, 7: False}  # New: armed state
        self.track_is_recording = {0: False, 1: False, 2: False, 3: False, 4: False, 5: False, 6: False, 7: False}  # New: recording state
        self.track_is_playing = {i: False for i in range(8)}
        self.clip_slot_index = 0  # Always using the first clip slot.
        self.validation_interval = 1.0  #7.0 worked but the faster the better # For background validation.
        self.validation_running = False
        self.validation_thread = None
        self.lock = threading.Lock()
    
    def mark_track_has_clip(self, track_index, has_clip=True):
        with self.lock:
            if track_index in self.track_has_clip:
                self.track_has_clip[track_index] = has_clip
                print(f"Internal state updated: Track {track_index+1} has clip: {has_clip}")

    def mark_track_is_armed(self, track_index, is_armed):
        with self.lock:
            if track_index in self.track_is_armed:
                self.track_is_armed[track_index] = is_armed
                print(f"Internal state updated: Track {track_index+1} is armed: {is_armed}")

    def get_track_is_armed(self, track_index):
        with self.lock:
            return self.track_is_armed.get(track_index, False)
    
    def get_next_empty_track(self, player):
        with self.lock:
            if player == 1:  # Player 1 tracks (1, 3, 5, 7)
                for track_idx in [0, 2, 4, 6]:
                    if not self.track_has_clip[track_idx]:
                        return track_idx
            elif player == 2:  # Player 2 tracks (2, 4, 6, 8)
                for track_idx in [1, 3, 5, 7]:
                    if not self.track_has_clip[track_idx]:
                        return track_idx
            return None
    
    def get_filled_tracks(self, player):
        with self.lock:
            if player == 1:
                return [idx for idx in [0, 2, 4, 6] if self.track_has_clip[idx]]
            elif player == 2:
                return [idx for idx in [1, 3, 5, 7] if self.track_has_clip[idx]]
    
    def get_empty_tracks(self, player):
        with self.lock:
            if player == 1:
                return [idx for idx in [0, 2, 4, 6] if not self.track_has_clip[idx]]
            elif player == 2:
                return [idx for idx in [1, 3, 5, 7] if not self.track_has_clip[idx]]
    
    def are_all_tracks_filled(self, player):
        with self.lock:
            if player == 1:
                return all(self.track_has_clip[idx] for idx in [0, 2, 4, 6])
            elif player == 2:
                return all(self.track_has_clip[idx] for idx in [1, 3, 5, 7])
    
    def start_background_validation(self, client, ip_addresses):
        if self.validation_thread is not None and self.validation_thread.is_alive():
            print("Background validation already running")
            return
        self.validation_running = True
        self.validation_thread = threading.Thread(
            target=self._background_validation_loop,
            args=(client, ip_addresses),
            daemon=True
        )
        self.validation_thread.start()
        print(f"Background validation started (every {self.validation_interval} seconds)")
    
    def stop_background_validation(self):
        self.validation_running = False
        if self.validation_thread:
            self.validation_thread.join(timeout=1.0)
            print("Background validation stopped")
    
    def _background_validation_loop(self, client, ip_addresses):
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
                self.validate_state_with_ableton(client)
                self.send_full_clip_state_update(client2_clients)
                print("=== Background validation complete ===\n")
            except Exception as e:
                print(f"Error in background validation: {e}")

    def validate_state_with_ableton(self, client):
        print("Validating internal clip state with Ableton...")
        for track_idx in range(8):  # Check all tracks 1-8
            has_clip = check_track_has_clip(client, track_idx, self.clip_slot_index)
            self.mark_track_has_clip(track_idx, has_clip)
            is_armed = check_track_is_armed(client, track_idx)
            self.mark_track_is_armed(track_idx, is_armed)
            is_recording = check_track_is_recording(client, track_idx, self.clip_slot_index)
            self.mark_track_is_recording(track_idx, is_recording)
            is_playing = check_track_is_playing(client, track_idx, self.clip_slot_index)
            self.mark_track_is_playing(track_idx, is_playing)

            filled_tracks = [t + 1 for t in self.get_filled_tracks(1)] + [t + 1 for t in self.get_filled_tracks(2)]
            empty_tracks = [t + 1 for t in self.get_empty_tracks(1)] + [t + 1 for t in self.get_empty_tracks(2)]
            print(f"Current state - Tracks with clips: {filled_tracks if filled_tracks else 'none'}")
            print(f"Current state - Empty tracks: {empty_tracks if empty_tracks else 'none'}")
            print(f"Current state - Armed tracks: {[i+1 for i, v in self.track_is_armed.items() if v]}")
            print(f"Current state - Recording tracks: {[i+1 for i, v in self.track_is_recording.items() if v]}")

        self.send_full_clip_state_update(client2_clients)

    def get_clip_presence_grid(self, client):
        """
        Generates a grid of 0s and 1s representing the presence of clips.
        The format is VROSC/t1/cs1/.../t8/cs3/...
        """
        grid = []

        for track_idx in range(8):  # Tracks 1 to 8
            for clip_slot in range(3):  # Clip slots 1 to 3
                # Query Ableton for the presence of a clip in the specified track and slot
                has_clip = check_track_has_clip(client, track_idx, clip_slot)
                grid.append(1 if has_clip else 0)  # Append 1 if there's a clip, otherwise 0

        return grid
    
    def send_full_clip_state_update(self, client2_clients):
        presence = [1 if self.track_has_clip[i] else 0 for i in range(8)]
        playing = [1 if self.track_is_playing[i] else 0 for i in range(8)]
        recording = [1 if self.track_is_recording[i] else 0 for i in range(8)]
        for c in client2_clients:
            c.send_message("/VROSC/clippresence", presence)
            c.send_message("/VROSC/clipisplaying", playing)
            c.send_message("/VROSC/clipisrecording", recording)
            print(f"Sent OSC to {c}: presence={presence}, playing={playing}, recording={recording}")

    def get_next_track(self, current_track, player):
        """
        Returns the next track index in the series based on the player.
        Wraps around to the first track if the end is reached.
        """
        if player == 1:
            designated_tracks = [0, 2, 4, 6]  # Tracks 1, 3, 5, 7
        else:
            designated_tracks = [1, 3, 5, 7]  # Tracks 2, 4, 6, 8

        current_index = designated_tracks.index(current_track)
        next_index = (current_index + 1) % len(designated_tracks)
        return designated_tracks[next_index]

    def mark_track_is_recording(self, track_index, is_recording):
        with self.lock:
            if track_index in self.track_is_recording:
                self.track_is_recording[track_index] = is_recording
                print(f"Internal state updated: Track {track_index+1} is recording: {is_recording}")

    def get_track_is_recording(self, track_index):
        with self.lock:
            return self.track_is_recording.get(track_index, False)

    def mark_track_is_playing(self, track_index, is_playing):
        with self.lock:
            if track_index in self.track_is_playing:
                self.track_is_playing[track_index] = is_playing
                print(f"Internal state updated: Track {track_index+1} is playing: {is_playing}")

    def get_track_is_playing(self, track_index):
        with self.lock:
            return self.track_is_playing.get(track_index, False)

    def reset(self):
        with self.lock:
            for i in range(8):
                self.track_has_clip[i] = False
                self.track_is_armed[i] = False
                self.track_is_recording[i] = False
                self.track_is_playing[i] = False
        print("StateTracker: All track states reset.")

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
    global base_clip_length, all_clips_recorded
    print(f"[DEBUG] Finalizing recording on track {track_index+1}, slot {clip_slot_index+1}...")

    time.sleep(1.0)

    max_attempts = 20
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

        # Mark finalized track as having a clip
        state_tracker.mark_track_has_clip(track_index, True)

        if base_clip_length is None and track_index == 0 and clip_slot_index == 0:
            base_clip_length = clip_length
            print(f"[DEBUG] Base clip length updated to {base_clip_length} beats (from first clip).")

        if state_tracker.are_all_tracks_filled(1) or state_tracker.are_all_tracks_filled(2):
            print("[DEBUG] All designated tracks now have clips. Starting synchronization after delay...")
            all_clips_recorded = True
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
    
    filled_tracks = state_tracker.get_filled_tracks(1) + state_tracker.get_filled_tracks(2)
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
def record_clip_player1(client, state_tracker):
    track_to_use = state_tracker.get_next_empty_track(1)
    if track_to_use is None:
        print("Player 1: All designated tracks (1, 3, 5, 7) are full! Clear some clips before recording more.")
        return

    print(f"Player 1: Recording new clip in track {track_to_use + 1}, slot {state_tracker.clip_slot_index + 1}")
    
    # Disarm only Player 1's tracks
    for i in [0, 2, 4, 6]:  # Player 1's designated tracks
        client.send_message("/live/track/set/arm", [i, 0])  # Disarm Player 1's tracks
    client.send_message("/live/track/set/arm", [track_to_use, 1])  # Arm the selected track
    client.send_message("/live/clip_slot/fire", [track_to_use, state_tracker.clip_slot_index])
    state_tracker.mark_track_has_clip(track_to_use, True)
    
    # Update the active track for Player 1
    global current_active_track_player1
    current_active_track_player1 = track_to_use
    print(f"Player 1: Recording started on track {track_to_use + 1}, slot {state_tracker.clip_slot_index + 1}")

def record_clip_player2(client, state_tracker):
    track_to_use = state_tracker.get_next_empty_track(2)
    if track_to_use is None:
        print("Player 2: All designated tracks (2, 4, 6, 8) are full! Clear some clips before recording more.")
        return

    print(f"Player 2: Recording new clip in track {track_to_use + 1}, slot {state_tracker.clip_slot_index + 1}")
    
    # Disarm only Player 2's tracks
    for i in [1, 3, 5, 7]:  # Player 2's designated tracks
        client.send_message("/live/track/set/arm", [i, 0])  # Disarm Player 2's tracks
    client.send_message("/live/track/set/arm", [track_to_use, 1])  # Arm the selected track
    client.send_message("/live/clip_slot/fire", [track_to_use, state_tracker.clip_slot_index])
    state_tracker.mark_track_has_clip(track_to_use, True)
    
    # Update the active track for Player 2
    global current_active_track_player2
    current_active_track_player2 = track_to_use
    print(f"Player 2: Recording started on track {track_to_use + 1}, slot {state_tracker.clip_slot_index + 1}")

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
    if result[0] is None:
        print(f"No response received for track {track_index+1}; assuming no clip.")
        return False
    return result[0]

def check_track_is_armed(client, track_index):
    print(f"Checking if track {track_index+1} is armed...")
    event = threading.Event()
    result = [None]
    def handler(unused_addr, *args):
        if len(args) >= 2:
            if int(args[0]) == track_index:
                result[0] = bool(args[1])
                event.set()
                print(f"Response: Track {track_index+1} is armed: {result[0]}")
    global_dispatcher.map("/live/track/get/arm", handler)
    client.send_message("/live/track/get/arm", [track_index])
    start_time = time.time()
    while not event.is_set() and time.time() - start_time < 0.5:
        time.sleep(0.01)
    global_dispatcher.unmap("/live/track/get/arm", handler)
    if result[0] is None:
        print(f"No response received for track {track_index+1}; assuming not armed.")
        return False
    return result[0]

def check_track_is_recording(client, track_index, clip_slot_index):
    print(f"Checking if track {track_index+1}, slot {clip_slot_index+1} is recording...")
    event = threading.Event()
    result = [None]
    def handler(unused_addr, *args):
        if len(args) >= 3:
            if int(args[0]) == track_index and int(args[1]) == clip_slot_index:
                result[0] = bool(args[2])
                event.set()
                print(f"Response: Track {track_index+1}, slot {clip_slot_index+1} is recording: {result[0]}")
    global_dispatcher.map("/live/clip/get/is_recording", handler)
    client.send_message("/live/clip/get/is_recording", [track_index, clip_slot_index])
    start_time = time.time()
    while not event.is_set() and time.time() - start_time < 0.5:
        time.sleep(0.01)
    global_dispatcher.unmap("/live/clip/get/is_recording", handler)
    if result[0] is None:
        print(f"No response received for track {track_index+1}, slot {clip_slot_index+1}; assuming not recording.")
        return False
    return result[0]

def check_track_is_playing(client, track_index, clip_slot_index):
    print(f"Checking if track {track_index+1}, slot {clip_slot_index+1} is playing...")
    event = threading.Event()
    result = [None]
    def handler(unused_addr, *args):
        if len(args) >= 3:
            if int(args[0]) == track_index and int(args[1]) == clip_slot_index:
                result[0] = bool(args[2])
                event.set()
                print(f"Response: Track {track_index+1}, slot {clip_slot_index+1} is playing: {result[0]}")
    global_dispatcher.map("/live/clip/get/is_playing", handler)
    client.send_message("/live/clip/get/is_playing", [track_index, clip_slot_index])
    start_time = time.time()
    while not event.is_set() and time.time() - start_time < 0.5:
        time.sleep(0.01)
    global_dispatcher.unmap("/live/clip/get/is_playing", handler)
    if result[0] is None:
        print(f"No response received for track {track_index+1}, slot {clip_slot_index+1}; assuming not playing.")
        return False
    return result[0]

# --- Simplified Clip Length Update Function ---
def update_clip_lengths(client, state_tracker):
    """
    Periodically checks the length of the clip in track 1, slot 1,
    and updates the lengths of the other clips to match.
    """
    if not state_tracker.track_has_clip[0]:  # Check if track 1 has a clip
        print("[INFO] Track 1, slot 1 has no clip. Skipping update.")
        return

    loop_points = query_clip_loop_points(client, 0, 0)  # Get loop points for track 1, slot 1
    if loop_points[0] is not None and loop_points[1] is not None:
        base_clip_length = loop_points[1] - loop_points[0]
        print(f"[INFO] Base clip length: {base_clip_length} beats")

        filled_tracks = state_tracker.get_filled_tracks(1) + state_tracker.get_filled_tracks(2)
        for track_idx in filled_tracks:
            print(f"Updating loop points for track {track_idx+1} to match length: {base_clip_length} beats")
            client.send_message("/live/clip/set/loop_start", [track_idx, state_tracker.clip_slot_index, 0.0])
            client.send_message("/live/clip/set/loop_end", [track_idx, state_tracker.clip_slot_index, base_clip_length])
    else:
        print("[ERROR] Unable to retrieve loop points for track 1, slot 1.")

# --- Main and Keyboard Handling ---
def main():
    global running  # Use the global flag
    global waiting_for_refire_player1, waiting_for_refire_player2, current_active_track_player1, current_active_track_player2, all_clips_recorded
    global client2_clients
    start_global_osc_server()
    state_tracker = StateTracker()  # Single StateTracker for both players
    ip = "127.0.0.1"   # AbletonOSC sending address
    port = 11000       # AbletonOSC sending port
    client = udp_client.SimpleUDPClient(ip, port)
    ip_addresses = ["192.168.1.26","192.168.1.211","192.168.1.11"]  # IP addresses for VR headsets
    # Create clients for PC Transmitter port (9001) to send messages to headsets
    client2_clients = [udp_client.SimpleUDPClient(ip, 9003) for ip in ip_addresses]
    start_client2_osc_server(client, client2_clients, state_tracker)
    
    print(f"Attempting to connect to AbletonOSC server at {ip}:{port}")
    if not verify_ableton_connection(client):
        input("Press Enter to exit...")
        return
    
    # Initial validation for both players
    state_tracker.validate_state_with_ableton(client)
    state_tracker.start_background_validation(client, ip_addresses)

    print("Foot controller started. Player 1 controls")
    print("- Press ',' to toggle recording/refiring")
    print("- Press '.' to stop all clips (playback only)")
    print("- Press '/' to fire scene")
    print("- Player 2 press ; ' \ to do same thing")
    print("- Press 's' to synchronize all clips to the same length")
    print("- Press 'up' and 'down' for other controls (not used here)")
    print("- Press 'esc' to exit")
    
    is_processing = False  # Local to main


    def handle_comma_press(e):
        global waiting_for_refire_player1, current_active_track_player1, is_processing_player1

        print("[DEBUG] --- Comma Key Pressed ---")
        print(f"[DEBUG] PRE: waiting_for_refire_player1={waiting_for_refire_player1}, current_active_track_player1={current_active_track_player1}, is_processing_player1={is_processing_player1}")
        print("Player 1: Comma key pressed.")

        with player1_lock:
            if is_processing_player1:
                print("Player 1: Already processing.")
                return
            is_processing_player1 = True

        try:
            if waiting_for_refire_player1:
                print(f"Player 1: Stopping track {current_active_track_player1 + 1}")
                client.send_message("/live/clip_slot/fire", [current_active_track_player1, state_tracker.clip_slot_index])
                # Send to VR headsets via PC Transmitter port (9001)
                if current_active_track_player1 == 0: 
                    for c in client2_clients:
                        #c.send_message("/clipisplaying", [1, current_active_track_player1, True])
                        c.send_message("/clipisrecording", [1, current_active_track_player1, False])
                elif current_active_track_player1 == 2: 
                    for c in client2_clients:
                        #c.send_message("/clipisplaying", [1, current_active_track_player1-1, True])
                        c.send_message("/clipisrecording", [1, current_active_track_player1-1, False])
                elif current_active_track_player1 == 4: 
                    for c in client2_clients:
                        #c.send_message("/clipisplaying", [1, current_active_track_player1 - 2, True])
                        c.send_message("/clipisrecording", [1, current_active_track_player1 - 2, False])
                elif current_active_track_player1 == 6: 
                    for c in client2_clients:
                        #c.send_message("/clipisplaying", [1, current_active_track_player1 - 3, True])
                        c.send_message("/clipisrecording", [1, current_active_track_player1 - 3, False])
                

                finalized_track = current_active_track_player1
                next_track = state_tracker.get_next_track(current_active_track_player1, 1)

                # Disarm all tracks and arm the next track
                for i in [0, 2, 4, 6]:
                    client.send_message("/live/track/set/arm", [i, 0])
                #client.send_message("/live/track/set/arm", [next_track, 1])
                #print(f"Player 1: Armed next track {next_track + 1}")

                with player1_lock:
                    current_active_track_player1 = next_track
                    waiting_for_refire_player1 = False
                    print("[DEBUG] Player 1: Set waiting_for_refire_player1 = False")

                threading.Thread(
                target=finalize_recording,
                args=(client, finalized_track, state_tracker.clip_slot_index, state_tracker),
                    daemon=True
                ).start()
            else:
                if current_active_track_player1 is None:
                    current_active_track_player1 = state_tracker.get_next_empty_track(1)
                    client.send_message("/live/track/set/arm",[8,1])
                    if current_active_track_player1 is None:
                        print("Player 1: No available tracks.")
                        return

                print(f"Player 1: Starting recording on track {current_active_track_player1 + 1}")

                for i in [0, 2, 4, 6]:
                    client.send_message("/live/track/set/arm", [i, 0])
                client.send_message("/live/track/set/arm", [current_active_track_player1, 1])
                client.send_message("/live/clip_slot/fire", [current_active_track_player1, state_tracker.clip_slot_index])
                # Send to VR headsets via PC Transmitter port (9001)
                if current_active_track_player1 == 0: 
                    for c in client2_clients:
                        c.send_message("/clipisrecording", [1, current_active_track_player1, True])
                elif current_active_track_player1 == 2: 
                    for c in client2_clients:
                        c.send_message("/clipisrecording", [1, current_active_track_player1-1, True])
                elif current_active_track_player1 == 4: 
                    for c in client2_clients:
                        c.send_message("/clipisrecording", [1, current_active_track_player1 - 2, True])
                elif current_active_track_player1 == 6: 
                    for c in client2_clients:
                        c.send_message("/clipisrecording", [1, current_active_track_player1 - 3, True])

                with player1_lock:
                    waiting_for_refire_player1 = True
                    print("[DEBUG] Player 1: Set waiting_for_refire_player1 = True")
        except Exception as e:
            print(f"ERROR in Player 1: {e}")
            with player1_lock:
                waiting_for_refire_player1 = False
        finally:
            with player1_lock:
                is_processing_player1 = False
                print(f"[DEBUG] POST: waiting_for_refire_player1={waiting_for_refire_player1}, current_active_track_player1={current_active_track_player1}, is_processing_player1={is_processing_player1}")

    def handle_semicolon_press(e):
        global waiting_for_refire_player2, current_active_track_player2, is_processing_player2
        print("[DEBUG] --- Semicolon Key Pressed ---")
        print(f"[DEBUG] PRE: waiting_for_refire_player2={waiting_for_refire_player2}, current_active_track_player2={current_active_track_player2}, is_processing_player2={is_processing_player2}")
        print("Player 2: Semicolon key pressed.")

        with player2_lock:
            if is_processing_player2:
                print("Player 2: Already processing.")
                return
            is_processing_player2 = True

        try:
            if waiting_for_refire_player2:
                print(f"Player 2: Stopping track {current_active_track_player2 + 1}")
                client.send_message("/live/clip_slot/fire", [current_active_track_player2, state_tracker.clip_slot_index])
                # Send to VR headsets via PC Transmitter port (9001)
                if current_active_track_player2 == 1: 
                    for c in client2_clients:
                        #c.send_message("/clipisplaying", [2, current_active_track_player2-1, True])
                        c.send_message("/clipisrecording", [2, current_active_track_player2-1, False])
                elif current_active_track_player2 == 3: 
                    for c in client2_clients:
                        #c.send_message("/clipisplaying", [2, current_active_track_player2 - 2, True])
                        c.send_message("/clipisrecording", [2, current_active_track_player2 - 2, False])
                elif current_active_track_player2 == 5: 
                    for c in client2_clients:
                        #c.send_message("/clipisplaying", [2, current_active_track_player2 - 3, True])
                        c.send_message("/clipisrecording", [2, current_active_track_player2 - 3, False])
                elif current_active_track_player2 == 7: 
                    for c in client2_clients:
                        #c.send_message("/clipisplaying", [2, current_active_track_player2 - 4, True])
                        c.send_message("/clipisrecording", [2, current_active_track_player2 - 4, False])

                finalized_track = current_active_track_player2
                next_track = state_tracker.get_next_track(current_active_track_player2, 2)

                # Disarm all tracks and arm the next track
                for i in [1, 3, 5, 7]:
                    client.send_message("/live/track/set/arm", [i, 0])
                #client.send_message("/live/track/set/arm", [next_track, 1])
                #print(f"Player 2: Armed next track {next_track + 1}")

                with player2_lock:
                    current_active_track_player2 = next_track
                    waiting_for_refire_player2 = False
                    print("[DEBUG] Player 2: Set waiting_for_refire_player2 = False")

                threading.Thread(
                target=finalize_recording,
                args=(client, finalized_track, state_tracker.clip_slot_index, state_tracker),
                    daemon=True
                ).start()
            else:
                if current_active_track_player2 is None:
                    current_active_track_player2 = state_tracker.get_next_empty_track(2)
                    client.send_message("/live/track/set/arm",[9,1])

                    if current_active_track_player2 is None:
                        print("Player 2: No available tracks.")
                        return

                print(f"Player 2: Starting recording on track {current_active_track_player2 + 1}")

                for i in [1, 3, 5, 7]:
                    client.send_message("/live/track/set/arm", [i, 0])
                client.send_message("/live/track/set/arm", [current_active_track_player2, 1])
                client.send_message("/live/clip_slot/fire", [current_active_track_player2, state_tracker.clip_slot_index])
                # Send to VR headsets via PC Transmitter port (9001)
                if current_active_track_player2 == 1: 
                    for c in client2_clients:
                        c.send_message("/clipisrecording", [2, current_active_track_player2-1, True])
                elif current_active_track_player2 == 3: 
                    for c in client2_clients:
                        c.send_message("/clipisrecording", [2, current_active_track_player2 - 2, True])
                elif current_active_track_player2 == 5: 
                    for c in client2_clients:
                        c.send_message("/clipisrecording", [2, current_active_track_player2 - 3, True])
                elif current_active_track_player2 == 7: 
                    for c in client2_clients:
                        c.send_message("/clipisrecording", [2, current_active_track_player2 - 4, True])

                with player2_lock:
                    waiting_for_refire_player2 = True
                    print("[DEBUG] Player 2: Set waiting_for_refire_player2 = True")
        except Exception as e:
            print(f"ERROR in Player 2: {e}")
            with player2_lock:
                waiting_for_refire_player2 = False
        finally:
            with player2_lock:
                is_processing_player2 = False
                print(f"[DEBUG] POST: waiting_for_refire_player2={waiting_for_refire_player2}, current_active_track_player2={current_active_track_player2}, is_processing_player2={is_processing_player2}")

    
    def stop_program(e):
        global running
        print("Exiting foot controller...")
        running = False  # Set the flag to False to stop the program

    
    ## Leave in case needs to be reimplemented
    # def force_validation(e):
    #     nonlocal is_processing
    #     with threading.Lock():
    #         if is_processing:
    #             print("Already processing a command. Please wait...")
    #             return
    #         is_processing = True
    #         try:
    #             print("Forcing state validation with Ableton...")
    #             validate_state_with_ableton(client, state_tracker)
    #         finally:
    #             is_processing = False
    
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
        
    # Fix keyboard bindings
    keyboard.on_press_key(',', handle_comma_press)
    keyboard.on_press_key('s', sync_all_clips)
    keyboard.on_press_key('esc', stop_program)
    
    # Map the Player 2 keyboard presses
    keyboard.on_press_key(';', handle_semicolon_press)
    keyboard.on_press_key('.', lambda e: stop_clips(client, client2_clients))
    keyboard.on_press_key("'", lambda e: stop_clips(client, client2_clients))
    keyboard.on_press_key('backslash', lambda e: fire_scene(client, client2_clients, state_tracker))
    keyboard.on_press_key('/', lambda e: fire_scene(client, client2_clients, state_tracker))

    # Start periodic updates
    def periodic_update():
        if running:  # Check if the program should continue running
            update_clip_lengths(client, state_tracker)
            threading.Timer(5.0, periodic_update).start()  # Check every 5 seconds
        else:
            state_tracker.stop_background_validation()
            if global_osc_server:
                global_osc_server.shutdown()  # Shutdown the OSC server
            if client2_osc_server:
                client2_osc_server.shutdown()  # Shutdown the client2 OSC server

    periodic_update()  # Start the periodic update

    # Main loop to keep the program running
    while running:
        time.sleep(0.1)  # Sleep briefly to avoid busy waiting

    print("Foot controller has stopped.")

# Function to safely print debug info
def debug_print_state():
    print("\n--- CURRENT STATE ---")
    print(f"Player 1: waiting_for_refire={waiting_for_refire_player1}, track={current_active_track_player1}, processing={is_processing_player1}")
    print(f"Player 2: waiting_for_refire={waiting_for_refire_player2}, track={current_active_track_player2}, processing={is_processing_player2}")
    print("--------------------\n")

if __name__ == "__main__":
    main()