#!/bin/env python3

import os
import subprocess
import json
import random

# Some global-level configuration
origin = "real-origin"
release_branch = "master"
pre_release_branch="pre-release"

# Utility functions

def printc(stmt):
    '''Print in cyan - used for printing actions that the script is going to take.'''
    print('\033[96m' + stmt + '\033[0m')

def printcn(stmt):
    '''Print in cyan without newline - see `printc`.'''
    print('\033[96m' + stmt + '\033[0m', end="")

def continue_step(prompt):
    '''Prompt the user to take a manual action that can't/shoudn't be automated, and wait for acknowledgement.'''
    printc(f'{prompt} (Press Return to continue)')
    input()

def yesno(prompt):
    '''Prompt the user for a yes/no question, default being 'yes' if the response is not given.'''
    printcn(f'{prompt} [Y/n] ')
    choice = input()
    if len(choice) == 0 or choice[0].lower() == 'y':
        return True
    return False


# Each function below denotes one step of the release process.

def init(old_version, new_version):
    printc("Starting release process.")
    printc(f"Checking out to {release_branch}, pulling, and then checking out to {pre_release_branch} for the remaining work")
    subprocess.call(["git", "checkout", release_branch])
    subprocess.call(["git", "pull", origin, release_branch, "-r"])
    subprocess.call(["git", "switch", "-C", pre_release_branch])
    continue_step(f"\nPlease rebase {pre_release_branch} onto {release_branch}.")

def import_bundle(old_version, new_version):
    continue_step(
        "\nUpdate the static bundle, and press return when done." +
        f"\nYou need to create a PR for it, and get it merged into {release_branch}." +
        f"I will checkout to {release_branch}, pull and then checkout to {pre_release_branch} again once that's done.")
    subprocess.call(["git", "checkout", release_branch])
    subprocess.call(["git", "pull", origin, release_branch, "-r"])
    subprocess.call(["git", "switch", "-C",{pre_release_branch}])
    continue_step(f"\nPlease rebase {pre_release_branch} onto {release_branch}.")

def review_changelog(old_version, new_version):
    subprocess.call(["git", "--no-pager", "log", f"HEAD...v{old_version}"])
    continue_step(f"\nReview the changelog, and press return when all entries added.")

def update_librdkafka_version_requirement(old_version, new_version):
    if yesno("Can I update the librdkafka version in kafka/00version.go?"):
        librdkafka_version = subprocess.check_output("grep '#define RD_KAFKA_VERSION' kafka/librdkafka_vendor/rdkafka.h | grep -o '0x........'", shell=True)
        librdkafka_version = librdkafka_version.strip()
        # strip the RC part from it.
        librdkafka_version = librdkafka_version[:-2].decode('latin1') + '00'
        printc(f"Hex version is {librdkafka_version}")
        subprocess.call(f"sed -i 's/#define MIN_RD_KAFKA_VERSION 0x......../#define MIN_RD_KAFKA_VERSION {librdkafka_version}/' kafka/00version.go", shell=True)
        subprocess.call(f"sed -i -E 's/librdkafka v[0-9\.]+/librdkafka v{new_version}/' kafka/00version.go", shell=True)
        printc("Updated 00version.go")

    if yesno("Can I update the librdkafka version in README.md?"):
        subprocess.call(f"sed -i -E 's/librdkafka [0-9\.]+/librdkafka {new_version}/' README.md", shell=True)
        printc("Updated README.md")

    if yesno("Can I update mk/doc-gen.py?"):
        subprocess.call(f"sed -i -E 's/\"v[0-9\.]+/\"v{new_version}/' mk/doc-gen.py", shell=True)
        printc("Updated doc-gen.py")

def major_version_check(old_version, new_version):
    if old_version.split('.')[0] == new_version.split('.')[0]:
        return
    continue_step("There is a major version change, please replace module name in the go.mod files, and whereever they are imported.")

def update_error_codes(old_version, new_version):
    continue_step("Going to generate errors")
    subprocess.call(["make", "-f",  "mk/Makefile", "generr"])
    printc("Generated errors.")

def generate_docs(old_version, new_version):
    continue_step("Going to generate docs. Make sure that godoc is installed and on the $PATH")
    printcn("Either specify a virtualenv path to activate, or leave empty to create a new one: ")
    venv = input()
    if len(venv) == 0:
        venv_name = f"/tmp/.releasevenv-{random.randint(0, 200)}"
        subprocess.call(["virtualenv", "--python", "/usr/bin/python3", venv_name])
        venv = f"{venv_name}/bin/activate"
    subprocess.call(f". {venv}; pip install beautifulsoup4; make -f mk/Makefile docs; deactivate", shell=True)
    printc("Generated docs.")

def clean_build(old_version, new_version):
    built = False
    while not built:
        try:
            for dir in ['kafka', 'schemaregistry', 'soaktest', 'kafkatest']:
                printc(f"Building {dir}")
                subprocess.call(f'cd {dir}; go clean; go build -v ./...', shell=True)
        finally:
            built = yesno("Are you happy with the result of the build?")

def run_tests(folder, servers = 'localhost:9092'):
    printc(f"Running tests in {folder}")
    failed = []
    popen = subprocess.Popen(f'cd {folder}; go test -json', shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for stdout_line in iter(popen.stdout.readline, b""):
        log = json.loads(stdout_line)
        if 'Test' not in log:
            continue
        if 'Action' not in log:
            continue

        if log["Action"] == "run":
            print(f"Running {log['Test']}")
        elif log["Action"] == "pass":
            print(f"Passed: {log['Test']}")
        elif log["Action"] == "fail":
            failed.append(log['Test'])
            print(f"Failed: {log['Test']}")
    popen.stdout.close()

    if len(failed) == 0:
        continue_step("All tests ran successfully. Press return to continue")
        return

    printc(f"Following tests failed: {', '.join(failed)}")
    failed_twice = []

    printc("Re-running failed tests to see if any was flaky")
    for test in failed:
        popen = subprocess.Popen(f'cd {folder}; go test -json -run ^{test}', shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for stdout_line in iter(popen.stdout.readline, b""):
            if 'Test' not in log:
                continue
            if 'Action' not in log:
                continue
            if log["Action"] == "run":
                print(f"Running {log['Test']}")
            elif log["Action"] == "pass":
                print(f"Passed: {log['Test']}")
            elif log["Action"] == "fail":
                failed_twice.append(log['Test'])
                print(f"Failed: {log['Test']}")
            elif log["Action"] == 'skip':
                print(f"Skipped: {log['Test']}")
        popen.stdout.close()

    if len(failed_twice) == 0:
        continue_step("All tests ran successfully. Press return to continue")
    else:
        continue_step(f"These tests failed twice: {','.join(failed_twice)}. Press return after fixing them manually.")

def run_tests_kafka(old_version, new_version):
    printcn("Please create your cluster and enter the bootstrap servers: ")
    servers = input()
    run_tests('kafka', servers)

def run_tests_schemaregistry(old_version, new_version):
    run_tests('schemaregistry')

def run_examples(old_version, new_version):
    printcn("Starting to run examples.\nPlease create your cluster and enter the cluster bootstrap servers [eg. localhost:9092]: ")
    servers = input()
    if servers == '':
        servers = 'localhost:9092'

    printcn("Enter the url of schemaregistry: [eg. http://localhost:8081/]")
    sr = input()

    example_dict = {
        "admin_alter_consumer_group_offsets": f"{servers} myGroup myTopic 0 10",
        "admin_create_acls": f"{servers} TOPIC topic1 LITERAL principal1 host1 ALL ALLOW",
        "admin_create_topic": f"{servers} myTopic 10 1",
        "admin_delete_acls": f"{servers} TOPIC myTopic LITERAL principal host1 ALL ALLOW",
        "admin_delete_consumer_groups": f"{servers} 30 myGroup",
        "admin_delete_topics": f"{servers} myTopic",
        "admin_describe_acls": f"{servers} TOPIC myTopic LITERAL milind milind ALL ALLOW",
        "admin_describe_config": f"{servers} TOPIC test",
        "admin_describe_consumer_groups": f"{servers} myGroup",
        "admin_list_consumer_group_offsets": f"{servers} myGroup false test4 0",
        "admin_list_consumer_groups": f"{servers}",
        "avro_generic_consumer_example": f"{servers} '{sr}' myGroup test4",
        "avro_generic_producer_example": f"{servers} '{sr}' test4",
        "avro_specific_consumer_example": f"{servers} '{sr}' myGroup test4",
        "avro_specific_producer_example": f"{servers} '{sr}' test4",
        "consumer_example": f"{servers} myGroup test4",
        "consumer_offset_metadata": f"{servers} myGroup2 test4 0 0 'x'",
        "cooperative_consumer_example": f"{servers} myGroup2 test4",
        "idempotent_producer_example": f"{servers} test4",
        "json_consumer_example": f"{servers} 'http://localhost:8081' myGroup3 test4",
        "json_producer_example": f"{servers} 'http://localhost:8081' test4",
        "json_producer_example": f"{servers} 'http://localhost:8081' test5",
        "json_consumer_example": f"{servers} 'http://localhost:8081' myGroup3 test5",
        "consumer_channel_example": f"{servers} myGroup3 test4 ",
        "producer_channel_example": f"{servers}",
        "producer_channel_example": f"{servers} test5",
        "producer_example": f"{servers} test5",
        "protobuf_producer_example": f"{servers} 'http://localhost:8081' test4",
        "protobuf_consumer_example": f"{servers} 'http://localhost:8081' myGroup4 test4",
        "stats_example": f"{servers} myGroup test4",
        "transactions_example": f"{servers}",
        "go-kafkacat" : f"--broker {servers} consume --group=myGroup3 test4",
        "library-version": "",
        "mockcluster_example": "",
        "producer_custom_channel_example": f"{servers} test4",
    }
    if servers == '':
        servers = 'localhost:9092'
    examples = next(os.walk('examples'))[1]
    skipped_examples = []
    errored_examples = []
    for example in sorted(examples):
        if example == 'legacy':
            printc("Skipping legacy examples")
            continue
        if example not in example_dict:
            skipped_examples.append(example)
            continue

        try:
            printc(f"Running {example}")
            subprocess.call(f"cd examples/{example}; go build; timeout 5 ./{example} {example_dict[example]}", shell=True)
            continue_step("Please inspect the result.")
        except Exception as e:
            printc("Exception:")
            print(e)
            errored_examples.append(example)

    if len(errored_examples) > 0:
        continue_step(f"Please check these errored examples:\n {','.join(errored_examples)}")
    if len(skipped_examples) > 0:
        continue_step(f"Please check these skipped examples manually:\n {', '.join(skipped_examples)}")

def merge_pre_release(old_version, new_version):
    continue_step(
        f"Please create a PR for the changes on {pre_release_branch}. " +
        f"After merging the PR into {release_branch}, I will checkout to {release_branch} and pull.")
    subprocess.call(["git", "checkout", release_branch])
    subprocess.call(["git", "pull", origin, release_branch, "-r"])

def tag(old_version, new_version):
    printc("Creating a local tag")
    subprocess.call(["git", "tag", f"v{new_version}"])
    printc("Dry-running the push of the tag")
    subprocess.call(["git", "push", "--dry-run", origin, f"v{new_version}"])
    continue_step(f"If the results of the dry-run were fine, run the following command:\n\tgit push {origin} v{new_version}\n")

def release_notes(old_version, new_version):
    continue_step(f"Create the release on https://github.com/confluentinc/confluent-kafka-go/releases/new?tag=v{new_version}")


# Finally, the code which actually runs the steps one by one

steps = [
        import_bundle,
        review_changelog,
        update_librdkafka_version_requirement,
        major_version_check,
        update_error_codes,
        generate_docs,
        clean_build,
        run_tests_kafka,
        run_tests_schemaregistry,
        run_examples,
        merge_pre_release,
        tag,
        release_notes]

def main():
    global pre_release_branch
    global post_release_branch
    printcn("The old version is: (eg. 1.9.0) ")
    old_version = input()
    printcn("The new version is: (eg. 2.0.2) ")
    new_version = input()
    pre_release_branch = f"dev_{pre_release_branch}_v{new_version}"
    init(old_version, new_version)

    for i in range(len(steps)):
        print(f"{i+1}. {steps[i].__name__}")
    printcn("Please press Enter to start at beginning, or enter a number to resume at a step: ")
    start = input()
    if start == '':
        start = 0
    else:
        start = int(start) - 1

    for i in range(start, len(steps)):
        printc(f"{i+1}. {steps[i].__name__} will be run.\n")
        steps[i](old_version, new_version)

    printc("Great work, the release is now complete!")

if __name__=='__main__':
    main()