# asr_demo

## Instructions to install the required packages in a new venv 

### Create a venv

```python -m venv /path/to/new/virtual/environment```



### Install all the required packages

```source /path/to/new/virtual/environment/bin/activate```

```pip install -r requirements.txt```


### Issues

you might need to install ffmpeg:

```sudo apt install ffmpeg```


### Run

python main.py --target_language="english" --sentence="Hello"
