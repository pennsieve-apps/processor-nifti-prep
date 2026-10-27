FROM python:3.12-slim

WORKDIR /processor

# nibabel + numpy for the reorientation, dcm2niix (its pip package ships the
# binary) for DICOM folders, and the Lambda runtime interface client so the
# same image can run as a Lambda function.
COPY requirements.txt /processor/requirements.txt
RUN pip install --no-cache-dir -r /processor/requirements.txt awslambdaric==2.2.1

COPY VERSION /processor/VERSION
COPY processor/ /processor/processor/

ENV PYTHONPATH="/processor"

# The platform runs the container as uid 1000; /tmp is the only scratch space.
USER 1000:1000

# Runs main() once as a container, or serves processor.handler as a Lambda
# function (see processor/entry.py).
ENTRYPOINT ["python3", "-m", "processor.entry"]
