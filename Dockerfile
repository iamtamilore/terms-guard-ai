FROM python:3.12-slim

# Run as a non-root user (Hugging Face Spaces requirement)
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    HF_HOME=/home/user/.cache/hf \
    SENTENCE_TRANSFORMERS_HOME=/home/user/.cache/st \
    TRANSFORMERS_CACHE=/home/user/.cache/hf \
    TOKENIZERS_PARALLELISM=false

WORKDIR /home/user/app

COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

COPY --chown=user . .

EXPOSE 7860
CMD ["python", "server.py"]
