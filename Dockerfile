FROM ubuntu:focal

USER pal
WORKDIR /pal
RUN git clone https://github.com/aandriella/asr_demo.git

CMD [ "/bin/bash" ]
