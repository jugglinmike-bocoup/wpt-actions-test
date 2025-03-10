import json
import logging
import os
import subprocess
import sys

import requests

here = os.path.abspath(os.path.dirname(__file__))
wpt_root = os.path.abspath(os.path.join(here, os.pardir, os.pardir))
active_label = 'pull-request-has-preview'

if not(wpt_root in sys.path):
    sys.path.append(wpt_root)

from tools.wpt.testfiles import get_git_cmd

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class Status(object):
    SUCCESS = 0
    FAIL = 1
    NEUTRAL = 78


def request(url, method_name, data=None, json_data=None, params=None,
            extra_headers=None):
    github_token = os.environ.get('GITHUB_TOKEN')
    headers = {
        'Authorization': 'token {}'.format(github_token),
        'Accept': 'application/vnd.github.machine-man-preview+json'
    }

    if extra_headers is not None:
        headers.update(extra_headers)

    kwargs = {
        'params': params,
        'headers': headers
    }
    method = getattr(requests, method_name)

    logger.info('Loading URL %s' % url)
    if json_data is not None or data is not None:
        kwargs['json'] = json_data
        kwargs['data'] = data

    resp = method(url, **kwargs)

    resp.raise_for_status()

    return resp.json()


def resource_exists(url):
    try:
        request(url, 'get')
    except requests.HTTPError as exception:
        if exception.response.status_code == 404:
            return False
        raise

    return True


class GitHub(object):
    def __init__(self, api_root, owner, repo):
        self.api_root = api_root
        self.owner = owner
        self.repo = repo

    def is_collaborator(self, login):
        return resource_exists(
            '{}/repos/{}/{}/collaborators/{}'.format(
                self.api_root, self.owner, self.repo, login
            )
        )

    def tag_exists(self, tag):
        return resource_exists(
            '{}/repos/{}/{}/git/refs/{}'.format(
                self.api_root, self.owner, self.repo, tag
            )
        )

    def create_tag(self, tag, sha):
        data = {
            'ref': 'refs/tags/{}'.format(tag),
            'sha': sha
        }
        url = '{}/repos/{}/{}/git/refs'.format(
            self.api_root, self.owner, self.repo
        )

        request(url, 'post', json_data=data)

    def update_tag(self, tag, sha):
        data = {
            'force': True,
            'sha': sha
        }
        url = '{}/repos/{}/{}/git/refs/{}'.format(
            self.api_root, self.owner, self.repo, tag
        )

        request(url, 'patch', json_data=data)

    def delete_tag(self, tag):
        url = '{}/repos/{}/{}/git/refs/{}'.format(
            self.api_root, self.owner, self.repo, tag
        )

        try:
            request(url, 'delete')
        except requests.HTTPError as exception:
            if exception.response.status_code != 404:
                raise

            logger.info(
                'Attempted to delete non-existent tag: {}'.format(tag_name)
            )

    def tag(self, tag, sha):
        if self.tag_exists(tag):
            self.update_tag(tag, sha)
        else:
            self.create_tag(tag, sha)

        logger.info('Tagged {} as {}'.format(sha, tag))

    def add_label(self, pr_number, label_name):
        data = {
            'labels': [label_name]
        }
        url = '{}/repos/{}/{}/issues/{}/labels'.format(
            self.api_root, self.owner, self.repo, pr_number
        )

        request(url, 'post', json_data=data)

    def remove_label(self, pr_number, label_name):
        url = '{}/repos/{}/{}/issues/{}/labels/{}'.format(
            self.api_root, self.owner, self.repo, pr_number, label_name
        )

        try:
            request(url, 'delete')
        except requests.HTTPError as exception:
            if exception.response.status_code != 404:
                raise

            logger.info(
                'Attempted to delete non-existent tag: {}'.format(tag_name)
            )


def main(api_root):
    with open(os.environ['GITHUB_EVENT_PATH']) as handle:
        event = json.load(handle)
        logger.info(json.dumps(event, indent=2))

    if 'pull_request' not in event:
        logger.info('Unexpected event data')
        return Status.FAIL

    owner, repo = os.environ['GITHUB_REPOSITORY'].split('/', 1)
    github = GitHub(api_root, owner, repo)
    action = event['action']
    pr_number = event['pull_request']['number']
    tag_name = 'pr_preview_{}'.format(pr_number)
    sha = event['pull_request']['head']['sha']
    is_open = event['pull_request']['closed_at'] is None
    login = event['pull_request']['user']['login']
    has_label = any(
        [label['name'] == active_label
        for label in event['pull_request']['labels']]
    )
    target_label = event.get('label', {}).get('name')


    if not is_open:
        if action == 'close' and has_label:
            # This operation will trigger another GitHub Action which will
            # subsequently delete the tag.
            github.remove_label(pr_number, active_label)
            return Status.SUCCESS

        return Status.NEUTRAL

    if action in ('open', 'reopen') and has_label:
        github.tag(tag_name, sha)
    elif action in ('open', 'reopen') and github.is_collaborator(login):
        # This operation will trigger another GitHub Action which will
        # subsequently create the tag.
        github.add_label(pr_number, active_label)
    elif action == 'label' and target_label == active_label:
        github.tag(tag_name, sha)
    elif action == 'unlabel' and target_label == active_label:
        github.delete_tag(tag_name)
    elif action == 'synchronize' and has_label:
        github.tag(tag_name, sha)
    else:
        return Status.NEUTRAL

    return Status.SUCCESS


if __name__ == '__main__':
    code = main(sys.argv[1])
    assert isinstance(code, int)
    sys.exit(code)
