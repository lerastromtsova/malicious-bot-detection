import base64
import concurrent
import json
import logging
import os
import re
import time
from datetime import datetime
from typing import Generator

import github
import pymongo
import requests
import vk.exceptions
from vk import API
from tqdm import tqdm


def parse_comment_ids(
        comment_id_source_repo: str,
        github_access_token: str,
        paths_to_data: list[str]
) -> dict:
    """
    Parses the VoynaSlov repository and returns the comment ids
    :param comment_id_source_repo: Reference to a Github repo with comment IDs
    :param github_access_token: Github access token
    :param paths_to_data: List of paths inside the repo that store the data
    :return: comment_ids
    """
    comment_id_file_name = comment_id_source_repo.replace('/', '_')
    if os.path.exists(f'/data/{comment_id_file_name}.json'):
        logging.info("Loading comment IDs from existing file...")
        with open(f'/data/{comment_id_file_name}.json') as json_file:
            comment_ids = json.load(json_file)
    else:
        logging.info(f"No existing data found. Parsing comment IDs "
                     f"from repository {comment_id_source_repo}.")
        comment_ids = {}
        g = github.Github(github_access_token)
        repo = g.get_repo(comment_id_source_repo)
        for path in paths_to_data:
            logging.info(f"Parsing path {path}")
            try:
                contents = repo.get_contents(path)
                while contents:
                    cur_file = contents.pop(0)
                    if cur_file.type == "dir":
                        contents.extend(repo.get_contents(cur_file.path))
                    else:
                        logging.info(f"Parsing file {cur_file}")
                        content = cur_file.content
                        content_str = base64.b64decode(content).decode('utf-8')
                        comment_ids[cur_file.path] = content_str.split('\n')
            except github.GithubException:
                pass
        with open(f'data/{comment_id_file_name}.json', 'w') as fp:
            json.dump(comment_ids, fp)
    logging.info("Comment IDs parsing finished")
    return comment_ids


def parse_comment_data(
        db_client: pymongo.MongoClient,
        api: API,
        db_writer
) -> Generator:
    """
    Parses comments on Vkontakte based on comment_ids.
    Writes the output to data/output dir.
    :param db_client:
    :param api: API to parse from
    :return:
    """
    comments = db_client.dataVKnodup.comments.find({
        "processed": False,
        "invalid": {"$ne": True}
    }).limit(25)
    comment_ids, media_ids = zip(*[
        (comment['vk_id'], str(comment['media_id']))
        for comment in comments])
    response = api.execute(
        code=f'var i = 0;'
             f'var comment;'
             f'var comments = [];'
             f'var comment_ids = {"[" + ",".join(comment_ids) + "]"};'
             f'var media_ids = {"[-" + ",-".join(media_ids) + "]"};'
             f'while (i != 25) {{'
             f'comment = API.wall.getComment('
             f'{{'
             f'"owner_id": (media_ids[i]), '
             f'"comment_id": (comment_ids[i]), '
             f'"v": "5.131", '
             f'"extended": "1"'
             f'}}'
             f'); '
             f'i = i + 1;'
             f'comments.push(comment);'
             f'}};'
             f'return comments;',
        v="5.131"
    )
    response = list(response)
    for i in range(len(response)):
        if not response[i]:
            response[i] = {
                'vk_id': comment_ids[i],
                'media_id': int(media_ids[i]),
                'processed': True,
                'invalid': True
            }
        else:
            response[i]['processed'] = True
        db_writer(response[i], db_client)
    return response


def delete_old_files() -> None:
    """
    Utility function to delete files older than 24.02.2022
    from the data directory.
    :return:
    """
    for root, dirs, files in os.walk("./data"):
        if root not in [
            './data',
            './data/independent',
            './data/state-affiliated',
            './data/output'
        ]:
            for file in files:
                if file != '.DS_Store':
                    date = file.split('.')[0]
                    d = datetime.strptime(date, '%Y-%m-%d')
                    date_24_02 = datetime.strptime('24-02-2022', '%d-%m-%Y')
                    if d < date_24_02:
                        os.remove(root + '/' + file)
                        logging.info(f'Removed file {file}')


def count_all_comments() -> int:
    """
    Provides information about the number of
    all comments in the data directory.
    :return:
    """
    total_count = 0
    for root, dirs, files in os.walk("./data"):
        if root not in [
            './data',
            './data/independent',
            './data/state-affiliated',
            './data/output'
        ]:
            for file in files:
                if file != '.DS_Store':
                    filepath = os.path.join(root, file)
                    with open(filepath, 'r') as f:
                        comment_ids = f.read().split('\n')
                        total_count += len(comment_ids)
    return total_count


def get_friends_of_friends(
        api: API
):
    my_friends = api.friends.get(v="5.131")['items']
    friends_of_friends = dict.fromkeys(my_friends)
    for i, friend in enumerate(my_friends):
        time.sleep(0.4)
        try:
            friends_of_friends[friend] = api.friends.get(
                user_id=friend,
                v="5.131"
            )['items']
        except vk.exceptions.VkAPIError:
            pass
        print(i)
    return friends_of_friends


def get_foaf_multithread(vk_user_ids):
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = []
        results = []
        for vk_user_id in vk_user_ids:
            futures.append(executor.submit(get_foaf_data, vk_user_id))
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())
    return results


def get_foaf_data(
        vk_user_id: str
) -> dict:

    url = f"https://vk.com/foaf.php?id={vk_user_id}"
    response = requests.get(url)
    xml = response.text
    created_at = None
    timezone = None
    followee_rate = None
    follower_rate = None
    follower_to_followee = None

    created_at_start_str = '<ya:created dc:date="'
    created_at_end_str = '"/>'
    created_at_str = re.search(
        f'{created_at_start_str}(.*){created_at_end_str}', xml
    )
    if created_at_str:
        created_at = created_at.group().split(
            created_at_start_str
        )[1].split(
            created_at_end_str
        )[0]
        created_at_tuple = created_at.split('+')
        created_at_date = datetime.strptime(
            created_at_tuple[0],
            '%Y-%m-%dT%H:%M:%S'
        )
        timezone = created_at_tuple[1]

    followee_rate_start_str = '<ya:subscribedToCount>'
    followee_rate_end_str = '</ya:subscribedToCount>'
    followee_rate_str = re.search(
        f'{followee_rate_start_str}(.*){followee_rate_end_str}',
        xml
    )
    if followee_rate_str:
        followee_rate = int(followee_rate_str.group().split(
            followee_rate_start_str
        )[1].split(
            followee_rate_end_str
        )[0])

    follower_rate_start_str = '<ya:subscribersCount>'
    follower_rate_end_str = '</ya:subscribersCount>'
    follower_rate_str = re.search(
        f'{follower_rate_start_str}(.*){follower_rate_end_str}',
        xml
    )
    if follower_rate_str:
        follower_rate = int(follower_rate_str.group().split(
            follower_rate_start_str
        )[1].split(
            follower_rate_end_str
        )[0])

    if follower_rate and followee_rate:
        follower_to_followee = round(follower_rate / followee_rate, 4)

    return {
        "vk_id": vk_user_id,
        "created_at": created_at_date,
        "timezone": timezone,
        "followee_rate": followee_rate,
        "follower_rate": follower_rate,
        "follower_to_followee": follower_to_followee
    }


def get_activity_count(
        vk_user_id: int,
        db_client: pymongo.MongoClient
) -> int:
    return db_client.dataVKnodup.comments.count_documents({
        'from_id': vk_user_id
    })


def get_friends_graph(
        users: list,
        api: API,
        db_client: pymongo.MongoClient,
        retrieve_friends_from_api: bool = True
) -> set:
    """
    Builds a graph of users based on their friendship relationship.
    :param users:
    :param api:
    :param db_client:
    :param retrieve_friends_from_api:
    :return: A set of edges between users.
    """
    vk_ids = set([user['vk_id'] for user in users])
    graph = set()
    if retrieve_friends_from_api:
        for i in tqdm(range(0, len(users), 25)):
            time.sleep(0.3)
            error_count = 0
            # 29 is the error code for quantity limit
            user_ids = [
                str(u['vk_id']) for u in users[i:i+25]
                if 'friends' not in u or u['friends'] == 29
            ]
            response = api.execute(
                code=f'var i = 0;'
                     f'var user;'
                     f'var users = [];'
                     f'var user_ids = {"[" + ",".join(user_ids) + "]"};'
                     f'while (i != 25) {{'
                     f'user = API.friends.get('
                     f'{{'
                     f'"user_id": (user_ids[i]), '
                     f'"v": "5.131", '
                     f'}}'
                     f'); '
                     f'i = i + 1;'
                     f'users.push(user);'
                     f'}};'
                     f'return users;',
                v="5.131"
            )
            response = list(response)
            for j in range(len(response)):
                if response[j]:
                    db_client.dataVKnodup.users.update_one(
                        {'vk_id': int(user_ids[j])},
                        {'$set': {'friends': response[j]['items']}}
                    )
                else:
                    db_client.dataVKnodup.users.update_one(
                        {'vk_id': int(user_ids[j])},
                        {'$set': {'friends': 30}}
                    )
                    error_count += 1
    else:
        for user in users:
            # If an integer is stored in "friends", it means the error code
            if isinstance(user['friends'], int):
                friends = set()
            else:
                friends = user['friends']
            inters = set(friends).intersection(vk_ids)
            if inters:
                for i in inters:
                    graph.add((user['vk_id'], i))
    return graph
