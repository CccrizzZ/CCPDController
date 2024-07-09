# pull the python base image
FROM python:3.12.2-bullseye

# set work directory
WORKDIR /usr/src/app

# set env variable
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# install dependencies from requirements txt file
RUN pip install --upgrade pip
COPY ./requirements.txt /usr/src/app
RUN pip install -r requirements.txt

# copy project
COPY . /usr/src/app

EXPOSE 8000

# dev
# CMD [ "python", "manage.py", "runserver_plus", "0.0.0.0:8000" ]

# prod
CMD ["gunicorn", "--bind", ":8000", "--workers", "3", "CCPDController.wsgi:application"]
