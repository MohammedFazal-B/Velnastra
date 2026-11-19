# Use an NVIDIA CUDA base image to compile llama-cpp-python with GPU support
FROM nvidia/cuda:12.8.1-devel-ubuntu22.04

# Prevent interactive prompts during apt install
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1

# Install Python 3, pip, and build tools
RUN apt-get update && apt-get install -y python3 python3-dev python3-pip cmake build-essential ninja-build && rm -rf /var/lib/apt/lists/*

# Set python3 as default python
RUN ln -sf /usr/bin/python3 /usr/local/bin/python

# Set the working directory inside the container
WORKDIR /app

# Copy the requirements file first to leverage Docker cache
COPY requirements.txt .

# Enable CUDA for llama-cpp-python compilation
ENV CMAKE_ARGS="-DGGML_CUDA=on"
ENV FORCE_CMAKE=1

# Install all dependencies using python -m pip
RUN python -m pip install --no-cache-dir --default-timeout=2000 -r requirements.txt

# Copy the rest of the application code
COPY . .

# Command to run the firewall
CMD ["python", "-m", "src.main"]
