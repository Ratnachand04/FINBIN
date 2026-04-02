FROM ollama/ollama:latest

# We copy the GGUF and the Modelfile into the container
# NOTE: binfin-mistral.gguf MUST be generated via the local_trainer scripts
# BEFORE you run docker compose up --build!
COPY Modelfile /Modelfile
COPY binfin-mistral.gguf /binfin-mistral.gguf

# Start the Ollama server locally in the background, create the custom model via Modelfile,
# and then shut it down so the new blob is safely persisted in the Docker Image layer
RUN nohup bash -c "ollama serve &" && \
    sleep 5 && \
    ollama create binfin-custom -f /Modelfile && \
    # Cleanup the huge gguf file to avoid bloating the final Docker image 2x since the blob is now extracted
    rm /Modelfile /binfin-mistral.gguf && \
    pkill ollama

EXPOSE 11434
