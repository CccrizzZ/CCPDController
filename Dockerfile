# pull the python base image
FROM python:3.12.2-bullseye

# set work directory
WORKDIR /usr/src/app

# set env variable
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# install chromium
RUN apt-get update && apt-get install -y \
    chromium-driver \
    chromium \
    xvfb \
    --no-install-recommends \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# install dependencies from requirements txt file
RUN pip install --upgrade pip
COPY ./requirements.txt /usr/src/app
RUN pip install -r requirements.txt

# copy project
COPY . /usr/src/app

# Set environment variables for Selenium
ENV DISPLAY=:99

EXPOSE 8000

# dev
CMD [ "python", "manage.py", "runserver_plus", "0.0.0.0:8000" ]

# prod
# CMD ["gunicorn", "--bind", ":8000", "--workers", "3", "CCPDController.wsgi:application"]
