#!/usr/bin/env python3
import sounddevice as sd
import numpy as np
import threading
import queue
import tkinter as tk
from tkinter import ttk
import keyboard
import json
import os

# ======================================
# CONFIGURATION
# ======================================
SAMPLE_RATE = 44100       # fréquence d'échantillonnage
CHANNELS = 1              # mono
CONFIG_FILE = 'config.json'

# Touches de contrôle par défaut
default_record_key = 'F9'
default_reset_key = 'F10'
default_pause_key = 'F11'

# ======================================
# ÉTAT GLOBAL
# ======================================
loop_buffer = np.empty((0, CHANNELS), dtype=np.float32)
buffer_lock = threading.Lock()
record_queue = queue.Queue()
monitor_queue = queue.Queue()
is_recording = False
is_playing = False
monitor_enabled = False
playback_index = 0
stream = None
monitor_stream = None

# Paramètres modifiables
record_key = default_record_key
reset_key = default_reset_key
pause_key = default_pause_key
input_idx = None
loop_idx = None
monitor_idx = None

# Périphériques disponibles
devices = sd.query_devices()
device_list = []
for idx, dev in enumerate(devices):
    device_list.append({
        'index': idx,
        'name': dev['name'],
        'hostapi': sd.query_hostapis()[dev['hostapi']]['name'],
        'max_input_channels': dev['max_input_channels'],
        'max_output_channels': dev['max_output_channels']
    })

# Charger paramètres depuis fichier
if os.path.exists(CONFIG_FILE):
    try:
        cfg = json.load(open(CONFIG_FILE, 'r'))
        record_key = cfg.get('record_key', record_key)
        reset_key = cfg.get('reset_key', reset_key)
        pause_key = cfg.get('pause_key', pause_key)
        last_input = cfg.get('input_name', None)
        last_loop = cfg.get('loop_name', None)
        last_monitor = cfg.get('monitor_name', None)
    except Exception:
        print('[Config] Échec du chargement, utilisation des valeurs par défaut')
else:
    last_input = last_loop = last_monitor = None

# ======================================
# CALLBACK AUDIO
# ======================================
def audio_callback(indata, outdata, frames, time, status):
    global playback_index, loop_buffer
    if status:
        print(status, flush=True)
    if is_recording:
        record_queue.put(indata.copy())
        outdata.fill(0)
        return
    if is_playing and loop_buffer.size > 0:
        with buffer_lock:
            length = loop_buffer.shape[0]
            end = playback_index + frames
            if end <= length:
                chunk = loop_buffer[playback_index:end]
            else:
                chunk = np.vstack((loop_buffer[playback_index:length], loop_buffer[0:end-length]))
            playback_index = end % length
        outdata[:] = chunk
        if monitor_enabled:
            monitor_queue.put(chunk + indata)
    else:
        outdata[:] = indata
        if monitor_enabled:
            monitor_queue.put(indata.copy())

# ======================================
# FONCTIONS LOOPER & MONITOR
# ======================================
# Mise à jour du voyant de statut
def update_indicator():
    color = 'red' if is_recording else 'grey'
    indicator_canvas.itemconfig(indicator_circle, fill=color)


def toggle_record():
    global is_recording, is_playing, loop_buffer, playback_index
    if not is_recording:
        is_recording = True
        is_playing = False
        update_indicator()
        print("[Looper] Enregistrement démarré")
    else:
        is_recording = False
        frames = []
        while not record_queue.empty(): frames.append(record_queue.get())
        if frames:
            with buffer_lock: loop_buffer = np.vstack(frames)
            playback_index = 0
            is_playing = True
            print(f"[Looper] Enr. arrêté, durée: {loop_buffer.shape[0]/SAMPLE_RATE:.2f}s")
        else:
            print("[Looper] Aucune donnée enregistrée")
        update_indicator()


def reset_loop():
    global loop_buffer, is_playing, playback_index, is_recording
    # Réinitialiser boucle et arrêter enregistrement
    with buffer_lock:
        loop_buffer = np.empty((0, CHANNELS), dtype=np.float32)
    is_recording = False
    # Vider le buffer d'enregistrement
    while not record_queue.empty():
        record_queue.get()
    is_playing = False
    playback_index = 0
    update_indicator()
    print("[Looper] Boucle réinitialisée")


def toggle_playback():
    global is_playing
    is_playing = not is_playing
    print(f"[Looper] Playback {'repris' if is_playing else 'en pause'}")


def toggle_monitor():
    global monitor_enabled
    monitor_enabled = not monitor_enabled
    print(f"[Looper] Retour voix {'activé' if monitor_enabled else 'désactivé'}")

# Enregistrement temporel: enregistre pendant X ms et fait automatiquement le looper
def record_for_duration():
    global is_recording, is_playing, loop_buffer, playback_index
    try:
        ms = int(duration_var.get())
    except Exception:
        print("[TimedRec] Durée invalide")
        return
    sec = ms / 1000.0
    # nettoyer et démarrer
    if is_recording:
        print("[TimedRec] Déjà enregistrement")
        return
    while not record_queue.empty(): record_queue.get()
    playback_index = 0
    is_recording = True
    is_playing = False
    update_indicator()
    print(f"[TimedRec] Enregistrement pour {ms} ms démarré")
    # arrêt programmé
    threading.Timer(sec, toggle_record).start()

# ======================================
# GESTION HOTKEYS
# ======================================
def update_hotkey(old_key, new_key, callback):
    try: keyboard.remove_hotkey(old_key)
    except: pass
    keyboard.add_hotkey(new_key, callback)

# ======================================
# MONITOR THREAD
# ======================================
def start_monitor_stream():
    global monitor_stream
    try:
        monitor_stream = sd.OutputStream(samplerate=SAMPLE_RATE,
                                         channels=CHANNELS,
                                         device=monitor_idx)
        monitor_stream.start()
        while True:
            data = monitor_queue.get()
            monitor_stream.write(data)
    except Exception as e:
        print(f"[Erreur Monitor]: {e}")

# ======================================
# STREAM PRINCIPAL
# ======================================
def start_stream():
    global stream
    try:
        stream = sd.Stream(samplerate=SAMPLE_RATE,
                           channels=(CHANNELS, CHANNELS),
                           callback=audio_callback,
                           device=(input_idx, loop_idx))
        stream.start()
        print(f"[Looper] Stream: in#{input_idx} -> loop out#{loop_idx}")
    except Exception as e:
        print(f"[Erreur Stream]: {e}")

# ======================================
# UI & DEVICE SELECTION
# ======================================
root = tk.Tk()
root.title("Loopy Looper")
# Chargement et application de l'icône de l'application
# Placez votre fichier 'logo.png' dans le même dossier que ce script
try:
    icon_img = tk.PhotoImage(file='logo.png')
    root.iconphoto(False, icon_img)
except Exception as e:
    print(f"[Icône] Impossible de charger 'logo.png' : {e}")

# Zone console intégrée
import sys
from tkinter.scrolledtext import ScrolledText

# Redirection des prints vers la console GUI
class TextRedirector(object):
    def __init__(self, widget):
        self.widget = widget
    def write(self, msg):
        self.widget.configure(state='normal')
        self.widget.insert(tk.END, msg)
        self.widget.see(tk.END)
        self.widget.configure(state='disabled')
    def flush(self):
        pass

# Création de la zone console à droite
console_frame = ttk.Frame(root)
console_frame.grid(row=0, column=3, rowspan=10, sticky='nsew', padx=5, pady=5)
console_text = ScrolledText(console_frame, state='disabled', width=40, height=20)
console_text.pack(fill='both', expand=True)

# Adapter la grille pour que la console prenne tout l'espace vertical
root.grid_columnconfigure(3, weight=1)

# Rediriger stdout et stderr
sys.stdout = TextRedirector(console_text)
sys.stderr = TextRedirector(console_text)

# Enregistrer config et quitter
def save_and_exit():
    config = {
        'record_key': record_key_var.get(),
        'reset_key': reset_key_var.get(),
        'pause_key': pause_key_var.get(),
        'input_name': input_combo.get(),
        'loop_name': loop_combo.get(),
        'monitor_name': monitor_combo.get()
    }
    try:
        json.dump(config, open(CONFIG_FILE, 'w'), indent=4)
        print('[Config] Enregistré')
    except Exception as e:
        print(f"[Config] Erreur enregistrement: {e}")
    # fermer streams
    try:
        stream.stop(); stream.close()
    except:
        pass
    try:
        monitor_stream.stop(); monitor_stream.close()
    except:
        pass
    root.destroy()

root.protocol("WM_DELETE_WINDOW", save_and_exit)

# Filtrer options sortie en fonction de l'entrée
def update_output_options(event=None):
    host = next(d['hostapi'] for d in device_list if d['name']==input_combo.get())
    # Options Looper uniques
    loop_opts = list(dict.fromkeys([d['name'] for d in device_list if d['hostapi']==host and d['max_output_channels']>0]))
    loop_combo['values'] = loop_opts
    if loop_combo.get() not in loop_opts: loop_combo.set(loop_opts[0] if loop_opts else '')
    mon_opts = loop_opts.copy()
    monitor_combo['values'] = mon_opts
    if monitor_combo.get() not in mon_opts: monitor_combo.set(mon_opts[0] if mon_opts else '')

# Sélecteur Input
tk.Label(root, text="Entrée:").grid(row=0,column=0,sticky='w')
# (Vous pouvez ajuster la largeur du menu via le paramètre width)
# Sélecteur Input (sans doublons)
input_combo = ttk.Combobox(root,
    values=list(dict.fromkeys([d['name'] for d in device_list if d['max_input_channels']>0])),
    state='readonly', width=40)
input_combo.grid(row=0,column=1,padx=5,pady=5)
input_combo.bind('<<ComboboxSelected>>', update_output_options)
if last_input in [d['name'] for d in device_list]: input_combo.set(last_input)
else: input_combo.current(0)

# Sélecteur Loop Output
ttk.Label(root, text="Sortie Looper:").grid(row=1,column=0,sticky='w')
# (Ajustez width pour la largeur du menu Looper)
loop_combo = ttk.Combobox(root, state='readonly', width=40)
loop_combo.grid(row=1,column=1,padx=5,pady=5)

# Sélecteur Monitor Output
ttk.Label(root, text="Sortie Monitor:").grid(row=2,column=0,sticky='w')
# (Ajustez width pour la largeur du menu Monitor)
monitor_combo = ttk.Combobox(root, state='readonly', width=40)
monitor_combo.grid(row=2,column=1,padx=5,pady=5)
update_output_options()
if last_loop in loop_combo['values']: loop_combo.set(last_loop)
if last_monitor in monitor_combo['values']: monitor_combo.set(last_monitor)

# Appliquer paramètres
def apply_settings():
    global input_idx, loop_idx, monitor_idx, stream, monitor_thread, record_key, reset_key, pause_key
    input_idx = next(d['index'] for d in device_list if d['name']==input_combo.get())
    loop_idx = next(d['index'] for d in device_list if d['name']==loop_combo.get())
    monitor_idx = next(d['index'] for d in device_list if d['name']==monitor_combo.get())
    # Mettre à jour touches
    record_key = record_key_var.get()
    reset_key = reset_key_var.get()
    pause_key = pause_key_var.get()
    # restart main stream
    if stream: stream.stop(); stream.close()
    start_stream()
    # restart monitor thread
    if 'monitor_thread' in globals():
        try: monitor_stream.stop(); monitor_stream.close()
        except: pass
    monitor_thread = threading.Thread(target=start_monitor_stream, daemon=True)
    monitor_thread.start()
    # hotkeys
    keyboard.unhook_all()
    keyboard.add_hotkey(record_key, toggle_record)
    keyboard.add_hotkey(reset_key, reset_loop)
    keyboard.add_hotkey(pause_key, toggle_playback)
    update_status()

apply_btn = ttk.Button(root, text="Appliquer", command=apply_settings)
apply_btn.grid(row=3,column=0,columnspan=2,pady=5)

# Hotkey entries
ttk.Label(root, text="Touche Record:").grid(row=4,column=0,sticky='w')
record_key_var = tk.StringVar(value=record_key)
record_entry = ttk.Entry(root, textvariable=record_key_var)
record_entry.grid(row=4,column=1); record_entry.bind('<Return>', lambda e: apply_settings())

ttk.Label(root, text="Touche Reset:").grid(row=5,column=0,sticky='w')
reset_key_var = tk.StringVar(value=reset_key)
reset_entry = ttk.Entry(root, textvariable=reset_key_var)
reset_entry.grid(row=5,column=1); reset_entry.bind('<Return>', lambda e: apply_settings())

ttk.Label(root, text="Touche Pause:").grid(row=6,column=0,sticky='w')
pause_key_var = tk.StringVar(value=pause_key)
pause_entry = ttk.Entry(root, textvariable=pause_key_var)
pause_entry.grid(row=6, column=1); pause_entry.bind('<Return>', lambda e: apply_settings())

# Champ durée et bouton d'enregistrement temporel (bouton séparé)
# Vous pouvez ajuster le placement (row) si besoin
duration_var = tk.IntVar(value=1000)  # durée en ms
ttk.Label(root, text="Durée (ms):").grid(row=10, column=0, sticky='w')
duration_entry = ttk.Entry(root, textvariable=duration_var, width=10)
duration_entry.grid(row=10, column=1, sticky='w', padx=5)
# Bouton d'enregistrement temporel séparé
ttk.Button(root, text="Record X ms", command=record_for_duration).grid(row=10, column=2, pady=5)

# Boutons Looper & Monitor
# Bouton Enregistrer
ttk.Button(root, text="Enregistrer", command=toggle_record).grid(row=7, column=0, pady=5)
# Bouton Reset
ttk.Button(root, text="Reset Loop", command=reset_loop).grid(row=7, column=1, pady=5)
# Bouton Monitor
ttk.Button(root, text="Toggle Monitor", command=toggle_monitor).grid(row=7, column=2, pady=5)

# Statut et Voyant
def update_status():
    status_var.set(f"Rec:{record_key} Reset:{reset_key} Pause:{pause_key}")
    update_indicator()
status_var = tk.StringVar()
status_label = ttk.Label(root, textvariable=status_var)
status_label.grid(row=8,column=0,columnspan=2)
# Voyant d'enregistrement
indicator_canvas = tk.Canvas(root, width=20, height=20, highlightthickness=0)
indicator_canvas.grid(row=8, column=2, padx=5)
indicator_circle = indicator_canvas.create_oval(2, 2, 18, 18, fill="grey")
update_status()

# Toujours au-dessus
tk.Label(root, text="").grid(row=9, column=0)  # espacement
always_on_top_var = tk.BooleanVar(value=False)
always_on_top_cb = ttk.Checkbutton(root, text="Toujours au-dessus", variable=always_on_top_var,
                                   command=lambda: root.attributes('-topmost', always_on_top_var.get()))
always_on_top_cb.grid(row=9, column=1, columnspan=1, pady=5)

# Initialisation
def init():
    apply_settings()
init()
root.mainloop()
