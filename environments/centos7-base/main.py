#!/usr/bin/env python3

import os
import sys
import stat
import json
import urllib2

from datetime import datetime
from subprocess import call
from string import Template

def error(message):
    log(Template('ERR: $message').substitute(message=message))
    sys.stdout.flush()

def warn(message):
    log(Template('WARN: $message').substitute(message=message))
    sys.stdout.flush()

def log(message):
    print(Template('[main] $message').substitute(message=message))
    sys.stdout.flush()


def finish(succeeded, info):
    """
    This function will attempt to read any results file, add our metadata to it,
    write it to disk, send everything to S3, and exit the program.

    If succeeded is false, we'll assume things failed badly enough that we shouldn't
    even try to read /grade/results/results.json.
    """

    if info['job_id'] is None:
        # Someone screwed up bad; without a job ID, we can't upload archives to
        # S3 or notify the webhook that we've failed
        error('job_id was not specified, so it\'s impossible to upload an archive')
        sys.exit(1)

    data = None
    if succeeded:
        try:
            with open('/grade/results/results.json') as json_data:
                data = json.load(json_data)
        except JSONDecodeError:
            pass
        except Exception:
            pass

    results = {}
    results['data'] = data
    results['start_time'] = info['start_time']
    results['end_time'] = datetime.utcnow().isoformat()

    # Note the distinction between grade/results/results.json and grade/results.json
    # The former is generated by the code that we're calling and will store
    # test results; the latter will hold the results file that we'll generate
    # that contains both test results and some addition metadata
    with open('grade/results.json', 'w+') as results_file:
        json.dump(results, results_file)

    # Now we can upload this results file to AWS
    if info['results_bucket']:
        s3_results_file = Template('s3://$bucket/job_$job.json').substitute(bucket=info['results_bucket'], job=info['job_id'])
        s3_results_push_ret = call(['aws', 's3', 'cp', '/grade/results.json', s3_results_file])
        if s3_results_push_ret is not 0:
            error('could not push results to S3')
    else:
        error('the results bucket was not specified')

    # Now let's make an archive of the /grade directory and send it back to S3
    # for storage
    if info['archives_bucket']:
        zip_ret = call(['tar', '-zcf', '/archive.tar.gz', '/grade/'])
        if zip_ret is not 0:
            error('error zipping up archive')
        else:
            s3_archive_file = Template('s3://$bucket/job_$job.tar.gz').substitute(bucket=info['archives_bucket'], job=info['job_id'])
            s3_archive_push_ret = call(['aws', 's3', 'cp', '/archive.tar.gz', s3_archive_file])
            if s3_archive_push_ret is not 0:
                error('could not push archive to S3')
    else:
        error('the archives bucket was not specified')

    # Finally, notify the webhook (if specified) that this grading job is done
    if info['webhook_url']:
        req = urllib2.Request(webhook_url)
        req.add_header('Content-Type', 'application/json')

        # response = urllib2.urlopen(req, )

    # We're all done now.
    sys.exit(0 if succeeded else 1)


def main():
    log('started')

    info = {}

    # We use the ISO-formatted string because python doesn't know how to
    # serialize datetimes to json values by default
    info['start_time'] = datetime.utcnow().isoformat()

    environ_error = False

    if 'JOB_ID' not in os.environ:
        error('job ID was not specified in the JOB_ID environment variable')
        info['job_id'] = None
        environ_error = True
    else:
        info['job_id'] = os.environ['JOB_ID']

    if 'S3_JOBS_BUCKET' not in os.environ:
        error('the S3 jobs bucket was not specified in the S3_JOBS_BUCKET environment variable')
        info['jobs_bucket'] = None
        environ_error = True
    else:
        info['jobs_bucket'] = os.environ['S3_JOBS_BUCKET']

    if 'S3_RESULTS_BUCKET' not in os.environ:
        error('the S3 results bucket was not specified in the S3_RESULTS_BUCKET environment variable')
        info['results_bucket'] = None
        environ_error = True
    else:
        info['results_bucket'] = os.environ['S3_RESULTS_BUCKET']

    if 'S3_ARCHIVES_BUCKET' not in os.environ:
        error('the S3 archives bucket was not specified in the S3_ARCHIVES_BUCKET environment variable')
        info['archives_bucket'] = None
        environ_error = True
    else:
        info['archives_bucket'] = os.environ['S3_ARCHIVES_BUCKET']

    if 'WEBHOOK_URL' not in os.environ:
        warn('the webhook callback url was not specified in the WEBHOOK_URL environment variable')
        info['webhook_url'] = None
    else:
        info['webhook_url'] = os.environ['WEBHOOK_URL']

    if environ_error:
        finish(False, info)

    job_id = info['job_id']
    jobs_bucket =info['jobs_bucket']

    log(Template('running job $job').substitute(job=job_id))

    # Load the job archive from S3
    s3_job_file = Template('s3://$bucket/job_$job.tar.gz').substitute(bucket=jobs_bucket, job=job_id)
    s3_fetch_ret = call(['aws', 's3', 'cp', s3_job_file, '/job.tar.gz'])
    if s3_fetch_ret is not 0:
        error('failed to load the job files from S3')
        finish(False, info)

    # Unzip the downloaded archive
    unzip_ret = call(['tar', '-xf', '/job.tar.gz', '-C', 'grade'])
    if unzip_ret is not 0:
        error('failed to unzip the job archive')
        finish(False, info)

    # Users can specify init.sh scripts in several locations:
    # 1. in a question's /tests directory (ends up in /grade/tests/)
    # 2. in an autograder (ends up in /grade/shared/)
    # 3. in an environment (ends up in /grade/)
    # We'll only ever run one script; people writing init.sh scripts can choose
    # to run others from the one that we call. Which one we run is determined by the
    # above ordering: if we find /grade/tests/init.sh, we'll run that, otherwise
    # if we find /grade/shared/init.sh, we'll run that, and so on.

    init_files = ['/grade/tests/init.sh', '/grade/shared/init.sh', '/grade/init.sh']
    found_init_script = False
    for file in init_files:
        if os.path.isfile(file):
            found_init_script = True
            call(['chmod', '+x', file])
            init_ret = call([file])
            if init_ret is not 0:
                error(Template('error executing $file').substitute(file=file))
                finish(False, info)
            break

    # If we get this far, we've run the init script!
    # Let's run the grading script now
    grading_script = '/grade/run.sh'

    if os.path.isfile(grading_script):
        chmod_ret = call(['chmod', '+x', grading_script])
        if chmod_ret is not 0:
            error(Template('Could not make $file executable').substitute(file=grading_script))
        else:
            run_ret = call([grading_script])
            if run_ret is not 0:
                error(Template('error executing $file').substitute(file=grading_script))
                finish(False, info)
    else:
        error(Template('$file not found').substitute(grading_script))
        finish(False, info)

    # If we got this far, we (probably) succeeded!
    finish(True, info)

    log('finishing')


if __name__ == '__main__':
    main()
