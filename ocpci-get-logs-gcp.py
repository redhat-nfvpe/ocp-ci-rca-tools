#!/usr/bin/env python3
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import argparse
from concurrent.futures import TimeoutError
from google.cloud import pubsub_v1
from google.cloud import storage
import logging
import os
import urllib.parse
from ocpcilogreduce import ocpci_logreduce
from ocpcilogreduce import splitall
import time
import json



def usage() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Hook into OCP CI GCS artifacts bucket for logfile access")

    parser.add_argument("project_id", help="Your Google Cloud project ID")
    parser.add_argument("topic_id", help="Your Google Cloud topic ID")
    parser.add_argument("subscription_id", help="Your Google Cloud subscription ID")

    return parser.parse_args()

def list_subscriptions_in_topic(project_id, topic_id):
    """Lists all subscriptions for a given topic."""

    publisher = pubsub_v1.PublisherClient()
    topic_path = publisher.topic_path(project_id, topic_id)# pylint: disable=no-member

    response = publisher.list_topic_subscriptions(request={"topic": topic_path})# pylint: disable=no-member
    for subscription in response:
        print(subscription)

def list_subscriptions_in_project(project_id):
    """Lists all subscriptions in the current project."""
    subscriber = pubsub_v1.SubscriberClient()
    project_path = f"projects/{project_id}"

    # Wrap the subscriber in a 'with' block to automatically call close() to
    # close the underlying gRPC channel when done.
    with subscriber:
        for subscription in subscriber.list_subscriptions(request={"project": project_path}):# pylint: disable=no-member
            print(subscription.name)

def get_ocp_logfiles(events_json_url):
    print(f"get_ocp_logfiles({events_json_url[:10]}) Made it!")


# def extract_ocp_events(event_file: Path) -> Path:
#     event_text_path = Path(str(event_file) + '.txt')
#     event_text_path.write_text("\n".join(
#         [event['message']
#          for event in json.load(open(event_file))["items"]
#         ]))
#     return event_text_path

def download_blob(bucket_name, source_blob_name, destination_file_name):
    """Downloads a blob from the bucket."""
    # bucket_name = "your-bucket-name"
    # source_blob_name = "storage-object-name"
    # destination_file_name = "local/path/to/file"

    storage_client = storage.Client()

    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(source_blob_name)
    blob.download_to_filename(destination_file_name)

    print(
        "Blob {} downloaded to {}.".format(
            source_blob_name, destination_file_name
        )
    )

def process_job(mdata):
    if "name" in mdata:
        mdata_id = mdata["name"]
    else:
        print("no 'id' in mdata")
        return(-1)

    mdata_list = splitall(mdata_id)
    print(f"mdata_id is {mdata_id}")

    bucket_name = "origin-ci-test"
    org_repo = mdata_list[2]
    pull_number = mdata_list[3]
    job_name = mdata_list[4]
    build_number = mdata_list[5]
    prlogs_pull_dir = "pr-logs/pull/"
    # prlogs_directory_dir = "pr_logs/directory"
    finished_json_path = prlogs_pull_dir + org_repo + "/" + pull_number + "/" + job_name + "/" + build_number + "/" + "finished.json"
    events_json_path =   prlogs_pull_dir + org_repo + "/" + pull_number + "/" + job_name + "/" + build_number + "/" + "artifacts/build-resources/events.json"

    storage_client = storage.Client()
    bucket = storage_client.get_bucket(bucket_name)
    chk_bucket = storage_client.bucket(bucket_name)

    finished_json_blob = bucket.blob(finished_json_path)
    print(f"finished_json_blob is {finished_json_blob}")
    finished_json = str(finished_json_blob.download_as_string())
    # print(f"finished_json is {finished_json}")

    stats = storage.Blob(bucket=chk_bucket, name=events_json_path).exists(storage_client)
    if stats == False:
        print(f"no events.json file {stats}")
        return(False)
    else:
        print(f"events.json file exists {stats}")

    if "\"result\":\"SUCCESS\"" in finished_json:
        # TBD: seed jobname LR model
        return(False)
    elif "\"result\":\"FAILURE\"" in finished_json:
        # get_ocp_logfiles(events_json)
        ocpci_logreduce(events_json_path)


def receive_messages(project_id, subscription_id, timeout=None):
    """Receives messages from a pull subscription."""
    subscriber = pubsub_v1.SubscriberClient()
    # The `subscription_path` method creates a fully qualified identifier
    # in the form `projects/{project_id}/subscriptions/{subscription_id}`
    subscription_path = subscriber.subscription_path(project_id, subscription_id)# pylint: disable=no-member

    def callback(message):
        mdata = str(message.data)
        # filter out messages we are not interested in
        # only interested in finished.json file events
        if mdata.find("finished.json") >= 0 and mdata.find("origin-ci-test/logs/") == -1:
            process_job(mdata)

        message.ack()

    streaming_pull_future = subscriber.subscribe(subscription_path, callback=callback)
    print(f"Listening for messages on {subscription_path}..\n")

    # Wrap subscriber in a 'with' block to automatically call close() when done.
    with subscriber:
        try:
            # When `timeout` is not set, result() will block indefinitely,
            # unless an exception is encountered first.
            streaming_pull_future.result(timeout=timeout)
        except TimeoutError:
            streaming_pull_future.cancel()
        
def receive_messages_with_flow_control(project_id, subscription_id, timeout=None):
    """Receives messages from a pull subscription with flow control."""
    subscriber = pubsub_v1.SubscriberClient()
    subscription_path = subscriber.subscription_path(project_id, subscription_id)# pylint: disable=no-member
    
    def callback(message):
        mdata = str(message.data)
        mdata = mdata[2:-3].replace('\\n', '')
        mdata = json.loads(mdata)
        # "/openshift_cluster-network-operator/" in mdata['name'] and \

        if "finished.json" in mdata['name'] and \
            "/logs/" not in mdata['id'] and \
                "/pr-logs/pull/batch/" not in mdata['name']:
            print("message accept with" + str(mdata))
            process_job(mdata)
        else:
            print("message reject")

        # filter out messages we are not interested in
        # only interested in finished.json file events
        # if mdata.find("finished.json") >= 0 and mdata.find("origin-ci-test/logs/") == -1 and mdata.find("origin-ci-test/pr-logs/pull/batch/") == -1:
        #     print("message accept with" + mdata)
        #     # process_job(mdata)
        # else:
        #     print("message reject")

        message.ack()

    # Limit the subscriber to only have ten outstanding messages at a time.
    flow_control = pubsub_v1.types.FlowControl(max_messages=10)

    streaming_pull_future = subscriber.subscribe(
        subscription_path, callback=callback, flow_control=flow_control
    )
    print(f"Listening for messages on {subscription_path}..\n")

    # Wrap subscriber in a 'with' block to automatically call close() when done.
    with subscriber:
        try:
            # When `timeout` is not set, result() will block indefinitely,
            # unless an exception is encountered first.
            streaming_pull_future.result(timeout=timeout)
        except TimeoutError:
            streaming_pull_future.cancel()

if __name__ == "__main__":
    args = usage()
    list_subscriptions_in_project(args.project_id)
    # receive_messages(args.project_id, args.subscription_id)
    receive_messages_with_flow_control(args.project_id, args.subscription_id)