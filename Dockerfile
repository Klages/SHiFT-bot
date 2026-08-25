# Use an official Python runtime as a parent image
# We're using a slim version to keep the image size small.
FROM python:3.9-slim

# Set the working directory inside the container
WORKDIR /app

# Copy the requirements file into the container at /app
COPY requirements.txt .
# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the current directory contents into the container
COPY . .
# Command to run the Python script when the container launches
# The script will run as a single, long-running process with the Flask app.
CMD ["python", "shift-bot.py"]