FROM apify/actor-python:3.13

COPY requirements.txt ./
RUN echo "Python version:" && python --version \
    && pip install --no-cache-dir -r requirements.txt \
    && echo "Installed packages:" && pip freeze

COPY . ./

CMD ["python3", "-m", "src.main"]
