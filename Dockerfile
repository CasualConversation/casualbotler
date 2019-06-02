FROM python:3
#FROM gcc


WORKDIR /app

COPY . /app

RUN adduser sopel
RUN mkdir -p /var/lib/casualbotler
RUN chmod 777 /var/lib/casualbotler
RUN chmod 777 /app
RUN pip install --no-cache-dir --trusted-host pypi.python.org -r requirements.txt

USER sopel:sopel
CMD sopel -c docker.cfg
