import runpod

import base64
import tempfile

import torch

device = 'cuda' if torch.cuda.is_available() else 'cpu'

import requests

# Maximum data size: 200MB
MAX_PAYLOAD_SIZE = 200 * 1024 * 1024

def download_file(url, max_size_bytes, output_filename, api_key=None):
    """
    Download a file from a given URL with size limit and optional API key.

    Args:
    url (str): The URL of the file to download.
    max_size_bytes (int): Maximum allowed file size in bytes.
    output_filename (str): The name of the file to save the download as.
    api_key (str, optional): API key to be used as a bearer token.

    Returns:
    bool: True if download was successful, False otherwise.
    """
    try:
        # Prepare headers
        headers = {}
        if api_key:
            headers['Authorization'] = f'Bearer {api_key}'

        # Send a GET request
        response = requests.get(url, stream=True, headers=headers)
        response.raise_for_status()  # Raises an HTTPError for bad requests

        # Get the file size if possible
        file_size = int(response.headers.get('Content-Length', 0))
        
        if file_size > max_size_bytes:
            print(f"File size ({file_size} bytes) exceeds the maximum allowed size ({max_size_bytes} bytes).")
            return False

        # Download and write the file
        downloaded_size = 0
        with open(output_filename, 'wb') as file:
            for chunk in response.iter_content(chunk_size=8192):
                downloaded_size += len(chunk)
                if downloaded_size > max_size_bytes:
                    print(f"Download stopped: Size limit exceeded ({max_size_bytes} bytes).")
                    return False
                file.write(chunk)

        print(f"File downloaded successfully: {output_filename}")
        return True

    except requests.RequestException as e:
        print(f"Error downloading file: {e}")
        return False

def transcribe(job):
    datatype = job['input'].get('type', None)
    engine = job['input'].get('engine', 'faster-whisper')
    #model_name = job['input'].get('model', 'ivrit-ai/whisper-large-v3-ct2')
    #can cause error - try "whisper-large-v3-ct2" if default is buggy.
    is_streaming = job['input'].get('streaming', False)
    language_input = job['input'].get('language_input', 'he')

    if not datatype:
        yield { "error" : "datatype field not provided. Should be 'blob' or 'url'." }

    if not datatype in ['blob', 'url']:
        yield { "error" : f"datatype should be 'blob' or 'url', but is {datatype} instead." }

    if not engine in ['faster-whisper', 'stable-whisper']:
        yield { "error" : f"engine should be 'faster-whsiper' or 'stable-whisper', but is {engine} instead." }

    if not language_input in ['he', 'en','fr']:
        yield { "error" : f"language should be 'he', 'en' or 'fr', but is {language_input} instead." }
    
    if language_input == 'he':
        model_name="ivrit-ai/whisper-large-v3-turbo-ct2"
    else:
        model_name="openai/whisper-large-v3-turbo"
    
    # Get the API key from the job input
    api_key = job['input'].get('api_key', None)

    d = tempfile.mkdtemp()

    audio_file = f'{d}/audio.mp3'

    if datatype == 'blob':
        mp3_bytes = base64.b64decode(job['input']['data'])
        open(audio_file, 'wb').write(mp3_bytes) 
    elif datatype == 'url':
        success = download_file(job['input']['url'], MAX_PAYLOAD_SIZE, audio_file, api_key)
        if not success:
            yield { "error" : f"Error downloading data from {job['input']['url']}" }
            return

    stream_gen = transcribe_core(engine, model_name, audio_file,language_input)

    if is_streaming:
        for entry in stream_gen:
            yield entry
    else:
        result = [entry for entry in stream_gen]
        yield { 'result' : result }

def transcribe_core(engine, model_name, audio_file,language_input):
    print('Transcribing...')

    if engine == 'faster-whisper':
        import faster_whisper
        model = faster_whisper.WhisperModel(model_name, device=device, compute_type='float16')

        segs, _ = model.transcribe(audio_file, language=language_input, word_timestamps=True)
    elif engine == 'stable-whisper':
        import stable_whisper
        model = stable_whisper.load_faster_whisper(model_name, device=device, compute_type='float16')

        res = model.transcribe(audio_file, language=language_input, word_timestamps=True)
        segs = res.segments

    ret = { 'segments' : [] }

    for s in segs:
        words = []
        for w in s.words:
            words.append( { 'start' : w.start, 'end' : w.end, 'word' : w.word, 'probability' : w.probability } )

        seg = { 'id' : s.id, 'seek' : s.seek, 'start' : s.start, 'end' : s.end, 'text' : s.text, 'avg_logprob' : s.avg_logprob, 'compression_ratio' : s.compression_ratio, 'no_speech_prob' : s.no_speech_prob, 'words' : words }

        yield seg

runpod.serverless.start({"handler": transcribe, "return_aggregate_stream": True})

