import logging

import pymongo  # type: ignore
from datetime import datetime
import os
from vk import API  # type: ignore
import bson


def write_comment_to_db(
        comment: dict,
        db_client: pymongo.MongoClient
) -> None:
    """
    A function that writes a comment to Mongo db.
    :param comment:
    {
        "items": [
            {
                "id": 5742639,
                "from_id": 476028004,
                "date": 1646080752,
                "text": "[id173699767|Елена], там все
                несколько сложнее. Если тупо печатать бумагу при
                нулевой ставке, будет гиперинфляция - как в РФ в
                начале 90ых.",
                "post_id": 5742449,
                "owner_id": -76982440,
                "parents_stack": [
                    5742516
                ],
                "likes": {
                    "can_like": 1,
                    "count": 2,
                    "user_likes": 0,
                    "can_publish": 1
                },
                "reply_to_user": 173699767,
                "reply_to_comment": 5742620
            }
        ],
        "profiles": [
            {
                "id": 476028004,
                "sex": 2,
                "screen_name": "xhdhdhdhdhdh",
                "photo_50": "https://vk.com/images/camera_50.png",
                "photo_100": "https://vk.com/images/camera_100.png",
                "online_info": {
                    "visible": true,
                    "last_seen": 1660068001,
                    "is_online": false,
                    "app_id": 2274003,
                    "is_mobile": true
                },
                "online": 0,
                "first_name": "Alexander",
                "last_name": "Smirnov",
                "can_access_closed": true,
                "is_closed": false
            }
        ],
        "groups": [],
        "can_post": true,
        "show_reply_button": true,
        "groups_can_post": true
    }
    :param db_client:
    :return:
    """
    db = db_client.dataVKnodup
    for item in comment['items']:
        item['date'] = datetime.utcfromtimestamp(item['date'])
        item['_id'] = item['id']
        del item['id']
    try:
        if comment['items']:
            db.comments.insert_many(comment['items'])
        if comment['groups']:
            db.groups.insert_many(comment['groups'])
        if comment['profiles']:
            db.users.insert_many(comment['profiles'])
    except pymongo.errors.BulkWriteError:
        logging.warning("Trying to insert duplicate key")


def check_num_of_collection(
        db_client: pymongo.MongoClient,
        collection: str
):
    db = db_client.dataVKnodup
    if collection == 'users':
        return db.users.count_documents({})
    return db.comments.count_documents({})


def delete_duplicates(
        db_client: pymongo.MongoClient,
        collection: str
):
    if collection == 'users':
        cursor = db_client.dataVKnodup.users.aggregate(
            [
                {"$group": {
                    "_id": "$id",
                    "unique_ids": {"$addToSet": "$_id"},
                    "count": {"$sum": 1}
                }},
                {"$match": {"count": {"$gte": 2}}}
            ]
        )
        response = []
        for doc in cursor:
            del doc["unique_ids"][0]
            for i in doc["unique_ids"]:
                response.append(i)
        db_client.dataVKnodup.users.delete_many({"_id": {"$in": response}})
        db_client.dataVKnodup.users.create_index("id")
    elif collection == 'comments':
        cursor = db_client.dataVKnodup.comments.aggregate(
            [
                {"$group": {
                    "_id": "$ID",
                    "unique_ids": {"$addToSet": "$_id"},
                    "count": {"$sum": 1}
                }},
                {"$match": {"count": {"$gte": 2}}}
            ]
        )
        response = []
        for doc in cursor:
            del doc["unique_ids"][0]
            for i in doc["unique_ids"]:
                response.append(i)

        db_client.dataVKnodup.comments.delete_many({"_id": {"$in": response}})
        db_client.dataVKnodup.comments.create_index("id")


def insert_comment_ids(
        db_client: pymongo.MongoClient,
        api: API
) -> None:
    for root, dirs, files in os.walk("./data"):
        if root not in [
            './data',
            './data/independent',
            './data/state-affiliated',
            './data/output'
        ]:
            media_name = root.split('/')[-1]
            media_id = api.utils.resolveScreenName(
                screen_name=media_name, v='5.131'
            )['object_id']
            for file in files:
                if file != '.DS_Store':
                    filepath = os.path.join(root, file)
                    with open(filepath, 'r') as f:
                        comment_ids = f.read().split('\n')
                        result = [{
                            "_id": bson.ObjectId(i),
                            "media_name": media_name,
                            "media_id": media_id,
                            "processed": False
                        } for i in comment_ids]
                        try:
                            db_client.dataVKnodup.comments.insert_many(result)
                        except pymongo.errors.BulkWriteError:
                            logging.warning("Trying to insert duplicate key")
