import time
import os, subprocess
import json
import gi
gi.require_version('Notify', '0.7')
from gi.repository import Notify, GObject, Peas, RB
from pypresence import Presence
from status_prefs import discord_status_prefs

DEFAULT_APPID = "589905203533185064"

class DiscordStatus(GObject.Object, Peas.Activatable):
    object = GObject.property(type=GObject.Object)

    def __init__(self):
        super(DiscordStatus, self).__init__()

        print(f"discord_status: GOBJECT SELF OBJECT: {self.object}")

        settings_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), "settings.json")
        
        with open(settings_path) as settings_file:
            self.settings = json.load(settings_file)

        self.version = subprocess.run(['rhythmbox', '--version'], stdout=subprocess.PIPE).stdout.decode('utf-8').capitalize()
        self.notify_available = False
        self.connected = False
        self.streaming = False
        self.stream_flag = False
        self.playing = False
        self.song_started_at = 0
        self.playing_date = 0
        self.elapsed_time = 0

    def send_notification(self, message):
        if self.notify_available and self.settings["show_notifs"]:
            Notify.Notification.new("Rhythmbox Discord Status Plugin", message).show()

    
    def do_activate(self):
        self.notify_available = Notify.init("rhythmbox_discord_status")

        try:
            self.rpc = Presence(self.settings["appid"] if "appid" in self.settings else DEFAULT_APPID)
            self.rpc.connect()
            self.connected = True
            self.send_notification("Connected to Discord")
        except ConnectionRefusedError as err:
            print("discord_status: failed to connect to discord:", err)
            self.send_notification(f"Failed to connect to discord: {err}\nRe-enable the plugin to retry")
            return

        sp = self.object.props.shell_player
        self.playing_song_changed_id  = sp.connect('playing-song-changed', self.on_playing_song_changed)
        self.playing_state_changed_id = sp.connect('playing-changed', self.on_playing_state_changed)
        self.elapsed_changed_id       = sp.connect('elapsed-changed', self.on_elapsed_changed)
        self.playing_changed_id       = sp.connect('playing-song-property-changed', self.on_playing_song_property_changed)

    def do_deactivate(self):
        sp = self.object.props.shell_player
        sp.disconnect(self.playing_song_changed_id)
        sp.disconnect(self.playing_state_changed_id)
        sp.disconnect(self.elapsed_changed_id)
        sp.disconnect(self.playing_changed_id)

        if self.connected:
            self.rpc.close()

        if self.notify_available:
            Notify.uninit()

    def get_current_song_info(self, sp):
        playing_entry = sp.get_playing_entry()
        if not playing_entry:
            return {
                "album": "Unknown",
                "title": "Unknown",
                "artist": "Unknown",
                "genre": "Unknown",
                "year": 0,
                "duration": 0
            }

        album = playing_entry.get_string(RB.RhythmDBPropType.ALBUM)
        title = playing_entry.get_string(RB.RhythmDBPropType.TITLE)
        artist = playing_entry.get_string(RB.RhythmDBPropType.ARTIST)
        duration = playing_entry.get_ulong(RB.RhythmDBPropType.DURATION)
        genre = playing_entry.get_string(RB.RhythmDBPropType.GENRE)
        year = playing_entry.get_ulong(RB.RhythmDBPropType.YEAR)

        # If there is anything with less than 2 characters, Discord won't show our presence
        # So, lets add a cool empty unicode character to the end
        if album and len(album) < 2:
            album = f"{album}​"
        if title and len(title) < 2:
            title = f"{title}​"
        if artist and len(artist) < 2:
            artist = f"{artist}​"
        if genre and len(genre) < 2:
            genre = f"{genre}​"

        print(f"discord_status: album={album} artist={artist} title={title} genre={genre} year={year}")
        return {
            "album": album or "Unknown",
            "title": title or "Unknown",
            "artist": artist or "Unknown",
            "genre": genre or "Unknown",
            "year": year or 0,
            "duration": duration or 0
        }

    def update_rpc(self, sp, playing):
        if not playing and not sp.get_playing_entry():
            self.playing = False

            self.rpc.clear()
        else:
            song_info = self.get_current_song_info(sp)
            title = song_info["title"]

            if self.streaming or self.stream_flag:
                self.rpc.update(
                    state="Radio stream",
                    details=title[0:127],
                    large_image="rhythmbox",
                    large_text=self.version,
                    small_image="play",
                    small_text="Streaming",
                    start=int(time.time())
                )
                return

            self.playing = playing

            artist = song_info["artist"]
            album = song_info["album"]
            genre = song_info["genre"]
            year = song_info["year"]
            pos = sp.get_playing_time().time
            
            details = f"{artist} - {title}"
            state = f'From "{album}" ({year}) | {genre}' if album != "Unknown" else f"{year} | {genre}"
            start_time = int(time.time()) if self.settings["time_style"] == 1 else int(time.time()) - pos
            end_time = (start_time + song_info["duration"] - pos) if self.settings["time_style"] == 1 else None

            self.rpc.update(
                state=state[0:127],
                details=details[0:127],
                large_image="rhythmbox",
                large_text=self.version,
                small_image="play" if playing else "pause",
                small_text="Playing" if playing else "Paused",
                start=start_time if playing else None,
                end=end_time if playing else None
            )

    def on_playing_song_changed(self, sp, entry):
        print(f"discord_status: playing song changed sp={sp} entry={entry}")

        if not sp.get_playing_entry():
            return

        self.song_started_at = int(time.time())
        self.playing_date = self.song_started_at
        self.elapsed_time = 0
        current_song_info = self.get_current_song_info(sp)

        self.streaming = current_song_info["duration"] == 0 and self.streaming
        
        self.update_rpc(sp, True)


    def on_playing_state_changed(self, sp, playing):
        print(f"discord_status: playing state changed sp={sp} playing={playing}")
        self.update_rpc(sp, playing)

    def on_elapsed_changed(self, sp, elapsed):
        print(f"discord_status: elapsed changed sp={sp} elapsed={elapsed}")

        if self.playing:
            self.playing_date += 1

        if self.playing_date - elapsed != self.song_started_at and elapsed != 0:
            self.playing_date = self.song_started_at + elapsed
            print("discord_status: elapsed changed too much")
            self.update_rpc(sp, True)


    def on_playing_song_property_changed(self, sp, uri, property, old, newvalue):
        print(f"discord_status: playing song property changed sp={sp} uri={uri} property={property} old={old} newvalue={newvalue}")
        if property == "rb:stream-song-title":
            self.streaming = True
            self.update_rpc(sp, True)
