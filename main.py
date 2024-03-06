#!/usr/bin/env python3
import argparse
import queue
import sys
import time
import wave
import json
import os

import sounddevice as sd
import soundfile as sf
import numpy  # Make sure NumPy is loaded before it is used in the callback
assert numpy  # avoid "imported but unused" message (W0611)

import whisper # if import whisper after vosk it gets stuck
from vosk import Model, KaldiRecognizer, SetLogLevel
from google.cloud import speech
import jiwer

class ASR():
    @staticmethod
    def compute_error_rate(reference, hypothesis):
        """Compute error rate between the reference and hypothesis sentences
           https://github.com/jitsi/jiwer
        
        Parameters:
            reference (str): reference sentence in the ground truth
            hypothesis (str): detected sentence from the ASR

        Returns:
            wer, mer, wil (tuple): word error rate, match error rate, word 
            information lost
        """
        
        # preprocess the sentences to avoid sensitivity to spaces, punctuation 
        # and uppercase characters
        reference = jiwer.RemovePunctuation()(reference)
        reference = jiwer.ToLowerCase()(reference)
        reference = jiwer.Strip()(reference)

        hypothesis = jiwer.RemovePunctuation()(hypothesis)
        hypothesis = jiwer.ToLowerCase()(hypothesis)
        hypothesis = jiwer.Strip()(hypothesis)

        
        output = jiwer.process_words(reference, hypothesis)
        wer = output.wer
        mer = output.mer
        wil = output.wil
        wip = output.wip
        cer = jiwer.cer(reference, hypothesis)
        
        return wer, mer, wil, wip, cer

    def openwhisper_recognition(self, model_name, file_name, language_id):
        """Open whisper recognition.
        
        Parameters:
            model_name (str): the model name used by the ASR
            file_name (str): wav filename  
            language_id (str): language id

        Returns:
            text (str): the recognised text 
        """
        
        start = time.time()
        model = whisper.load_model(model_name, device='cpu')
        sentence = model.transcribe(file_name, fp16=False, language=language_id)
        end = time.time()
        print(f'sentence: {sentence["text"]}, in {end-start} secs')
        return sentence["text"], end-start
    
    def google_recognition(self, config, file_name):
        """Google asr recognition.
        
        Parameters:
            config (str): RecognitionConfig object generated by the ASR
            file_name (str): wav filename  

        Returns:
            text (str): the recognised text 
        """
        # create client instance
        start = time.time()
        client = speech.SpeechClient()
        with open(file_name, 'rb') as wf:
            content = wf.read()
            audio = speech.RecognitionAudio(content=content)
        sentence = ""

        # Sends the request to google to transcribe the audio
        response = client.recognize(request={"config": config, "audio": audio})
        for result in response.results:
            print("Transcript: {}".format(result.alternatives[0].transcript))
            sentence += result.alternatives[0].transcript
        
        end = time.time() 
        print(f'sentence: {sentence}, in {end-start} secs')
        return sentence, end-start
    
    def vosk_recognition(self, model_name, file_name):
        """Vosk recognition.
                
        Parameters:
            model_name (str): the name of the model
            file_name (str): wav filename  

        Returns:
            text (str): the recognised text 
        """
        wf = wave.open(file_name, "rb")
        model = Model(model_name=model_name)
        rec = KaldiRecognizer(model, wf.getframerate())
        rec.SetWords(True)
        rec.SetPartialWords(True)
        text = ""
        start = time.time()
        while True:
            data = wf.readframes(4000)
            if len(data) == 0:
                break
            if rec.AcceptWaveform(data):
                jres = json.loads(rec.Result())
                text = text + " " + jres["text"]
        jres = json.loads(rec.FinalResult())
        sentence = text + " " + jres["text"]
        end = time.time() 
        print(f'sentence: {sentence}, in {end-start} secs')
        return sentence, end-start
    

def int_or_str(text):
    """Helper function for argument parsing."""
    try:
        return int(text)
    except ValueError:
        return text

def callback(indata, frames, time, status):
    """This is called (from a separate thread) for each audio block."""
    if status:
        print(status, file=sys.stderr)
    q.put(indata.copy())


parser = argparse.ArgumentParser(add_help=False)

parser.add_argument(
    '-l', '--list-devices', action='store_true',
    help='show list of audio devices and exit')
args, remaining = parser.parse_known_args()
if args.list_devices:
    print(sd.query_devices())
    parser.exit(0)
parser = argparse.ArgumentParser(
    description=__doc__,
    formatter_class=argparse.RawDescriptionHelpFormatter,
    parents=[parser])
parser.add_argument(
    'filename', nargs='?', metavar='FILENAME',
    help='audio file to store recording to')
parser.add_argument(
    '-d', '--device', type=int_or_str,
    help='input device (numeric ID or substring)')
parser.add_argument(
    '-r', '--samplerate', type=int, help='sampling rate')
parser.add_argument(
    '-c', '--channels', type=int, default=1, help='number of input channels')
parser.add_argument(
    '-t', '--subtype', type=str, help='sound file subtype (e.g. "PCM_24")')
parser.add_argument(
        '--target_language', type=str, help='target language')
parser.add_argument(
        '--native_language', type=str, help='native language')
parser.add_argument(
        '--sentence', type=str, help='sentence to translate')

args = parser.parse_args(remaining)


q = queue.Queue()
asr = ASR()
SetLogLevel(-1)

google_model = "default"
google_language_id = {"english":"en-GB",
                      "italian":"it-IT",
                      "spanish":"es-ES",
                      "catalan":"ca-ES",
                      "french":"fr-FR"}
whisper_model = ["base", "large"]
vosk_models_by_language = {"english":{'base':'vosk-model-small-en-us-0.15', 'large':'vosk-model-en-us-0.42-gigaspeech'},
                           "italian":{'base':'vosk-model-small-it-0.22', 'large':'vosk-model-it-0.22'},
                           "spanish":{'base':'vosk-model-small-es-0.42', 'large':'vosk-model-es-0.42'},
                           "catalan":{'base':'vosk-model-small-ca-0.4', 'large':'vosk-model-small-ca-0.4'},
                           "french":{'base':'vosk-model-small-fr-0.22', 'large':'vosk-model-fr-0.22'}}

try:
    if args.samplerate is None:
        device_info = sd.query_devices(args.device, 'input')
        # soundfile expects an int, sounddevice provides a float:
        args.samplerate = int(device_info['default_samplerate'])
    if args.filename is None:
        args.filename = "recording.wav"
        # Check if the file already exists in the directory and remove it if it does
        if os.path.exists(args.filename):
            os.remove(args.filename)
            print(f"Removed existing file: {args.filename}")

    # Make sure the file is opened before recording anything:
    with sf.SoundFile(args.filename, mode='x', samplerate=args.samplerate,
                      channels=args.channels, subtype=args.subtype) as file:
        with sd.InputStream(samplerate=args.samplerate, device=args.device,
                            channels=args.channels, callback=callback):
            print('#' * 80)
            print('press Ctrl+C to stop the recording')
            print('#' * 80)
            while True:
                file.write(q.get())
except KeyboardInterrupt:
    print('\nRecording finished: ' + repr(args.filename))
    dir = os.getcwd()
    dir_filename =  dir+ '/' +args.filename
    
   
   
    # config = speech.RecognitionConfig(
    #         encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
    #         enable_automatic_punctuation=True,
    #         audio_channel_count=1,
    #         language_code=google_language_id,
    #     )
    # google_sentence, google_time = asr.google_recognition(config, dir_filename)
    
    whisper_s_model_sentence, _ = asr.openwhisper_recognition(model_name="base", file_name=dir_filename, language_id=args.target_language)
    ws_wer, ws_mer, ws_wil, ws_wip, ws_cer  = asr.compute_error_rate("Test", whisper_s_model_sentence)
    whisper_l_model_sentence, _ = asr.openwhisper_recognition(model_name="large", file_name=dir_filename, language_id=args.target_language)
    wl_wer, wl_mer, wl_wil, wl_wip, wl_cer  = asr.compute_error_rate("Test", whisper_l_model_sentence)
   
    
    vosk_small_model = vosk_models_by_language[args.target_language]['base']
    vosk_s_model_sentence, _ = asr.vosk_recognition(vosk_small_model, dir_filename)
    vs_wer, vs_mer, vs_wil, vs_wip, vs_cer  = asr.compute_error_rate("Test", vosk_s_model_sentence)

    vosk_large_model = vosk_models_by_language[args.target_language]['large']
    vosk_l_model_sentence, _ = asr.vosk_recognition(vosk_large_model, dir_filename)
    vl_wer, vl_mer, vl_wil, vl_wip, vl_cer  = asr.compute_error_rate("Test", vosk_l_model_sentence)
  
  # Define the data
    model_data = [
        ('vosk_small', vs_wer, vs_mer, vs_wil, vs_wip, vs_cer),
        ('vosk_large', vl_wer, vl_mer, vl_wil, vl_wip, vl_cer),
        ('whisper_small', ws_wer, ws_mer, ws_wil, ws_wip, ws_cer),
        ('whisper_large', wl_wer, wl_mer, wl_wil, wl_wip, wl_cer)
    ]

    # Calculate the maximum width for each column
    column_widths = [max(len(str(model[i])) for model in model_data) for i in range(len(model_data[0]))]

    # Print the header
    print("+" + "-"*(sum(column_widths) + 13) + "+")
    print(f"| {'Model':<{column_widths[0]}} | {'WER':<{column_widths[1]}} | {'MER':<{column_widths[2]}} | {'WIL':<{column_widths[3]}} | {'WIP':<{column_widths[4]}} | {'CER':<{column_widths[5]}} |")
    print("+" + "-"*(sum(column_widths) + 13) + "+")

    # Print the data
    for model_info in model_data:
        model_name = model_info[0]
        model_values = model_info[1:]
        print(f"| {model_name:<{column_widths[0]}} |", end=' ')
        for i, value in enumerate(model_values):
            print(f"{value:<{column_widths[i+1]}} |", end=' ')
        print()

    # Print the footer
    print("+" + "-"*(sum(column_widths) + 13) + "+")
    
    parser.exit(0)
except Exception as e:
    parser.exit(type(e).__name__ + ': ' + str(e))