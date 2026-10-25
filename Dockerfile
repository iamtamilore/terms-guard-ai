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

# The bytecode cache is keyed on the source mtime, and COPY gives every file a
# fresh mtime, so a stale .pyc can be loaded in preference to the new .py.
# Remove it after the copy so the source in the image is the source that runs.
RUN rm -rf __pycache__ && find . -name '*.pyc' -delete

EXPOSE 7860
CMD ["python", "server.py"]
