#  -*- coding: utf-8 -*-

import os
import argparse
import logging
from datetime import datetime, timedelta

import pytz
import sentry_sdk
from dotenv import load_dotenv

import tgtg
from tgtg import TgtgClient

from db import DbWrapper
from notifier import Notifier

parser= argparse.ArgumentParser()
parser.add_argument("-l","--login", help="Re-login given e-mail address even if tgtg token present in database", action='append')
args = parser.parse_args()

load_dotenv()

logging.basicConfig(encoding="utf-8", level=logging.INFO)

if os.environ.get("SENTRY_SDK_URL"):
    sentry_sdk.init(
        os.environ.get("SENTRY_SDK_URL"),
        traces_sample_rate=1.0,
        environment=os.environ.get("SENTRY_SDK_ENVIRONMENT"),
    )

# Init and open database
db = DbWrapper()
users = db.get_users()
force_login_user_emails = set(args.login or [])

local_tz = pytz.timezone("America/New_York")
now = datetime.now(local_tz)

for user in users:
    user_id = user["user_id"]
    user_email = user["email"]
    user_access_token = user["access_token"]
    # Init user notifier
    notifier = Notifier(user)

    if user_email in force_login_user_emails or not user_access_token:
        # login with too good to go token
        logging.info("User {} new login".format(user_email))
        tgtg_client = TgtgClient(email=user_email)

        credentials = tgtg_client.get_credentials()
        user_id=credentials["user_id"]
        user_access_token = credentials["access_token"]
        db.update_user(
            user_email,
            user_id,
            user_access_token,
            credentials["refresh_token"],
            credentials["cookie"],
        )

    else:
        if user_access_token == "INVALID":
            logging.info("User {} new login needed using --login".format(user_email))
            continue

        tgtg_client = TgtgClient(
            access_token=user_access_token,
            refresh_token=user["refresh_token"],
            user_id=user["user_id"],
            cookie=user["cookie"],
        )

    logging.info("User {}".format(user_email))

    # You can then get items (as default it will get your favorites)
    try:
        stores = tgtg_client.get_items()
    except tgtg.exceptions.TgtgAPIError as e:
        logging.error( "tgtg_client.get_items for user {} access {} refresh {} cookie {} resulted in Tgtg API error: {}".format( 
            user_email,
            tgtg_client.access_token,
            tgtg_client.refresh_token,
            tgtg_client.cookie,
            e))
        error_message = e.args[1].decode('utf-8')
        if "UNAUTHORIZED" in error_message.upper():
            text="clearing credentials for user {}".format(user_email)
            logging.info(text)
            notifier.send_notification(text)
            db.update_user(
               user_email,
               user_id,
               "INVALID","INVALID","INVALID")
        raise

    notifier = Notifier(user)
    favorite_stores = db.user_favorite_stores(user_id)

    for store in stores:
        s = store["store"]
        store_name=s["store_name"]
        store_branch=s.get("branch") or ""
        store_id = int(s["store_id"])
        item = store["item"]
        item_id = int(item["item_id"])
        item_name = item.get("name") or "item(s)"
        if (items_available:=store["items_available"]) > 0:
            for favorite_store in favorite_stores:
                if (
                    favorite_store["store_id"] == store_id
                    and favorite_store["item_id"] == item_id
                    and favorite_store["nb_item"] == 0
                ):

                    # Get UTC pickup date
                    pickup_from_utc = datetime.strptime(
                        store["pickup_interval"]["start"], "%Y-%m-%dT%H:%M:00Z"
                    )
                    pickup_latest_utc = datetime.strptime(
                        store["pickup_interval"]["end"], "%Y-%m-%dT%H:%M:00Z"
                    )

                    # Translate pickup date in local datetime
                    pickup_from = pickup_from_utc.replace(tzinfo=pytz.utc).astimezone(
                        local_tz
                    )
                    pickup_latest = pickup_latest_utc.replace(
                        tzinfo=pytz.utc
                    ).astimezone(local_tz)

                    tomorrow_date = now.date() + timedelta(days=1)
                    day = None

                    # Convert pickup date
                    if (
                        pickup_from.date() == now.date()
                        and pickup_latest.date() == now.date()
                    ):
                        day = "today"

                    elif (
                        pickup_from.date() == tomorrow_date
                        and pickup_latest.date() == tomorrow_date
                    ):
                        day = "tomorrow"

                    elif pickup_from.date() == pickup_latest.date():
                        day = pickup_from.strftime("%d-%m-%Y")

                    # If same pickup day
                    if day:
                        text = (
                            "{} new {} at {} {}, pickup {} between {} and {}".format(
                                items_available,
                                item_name,
                                store_name,
                                store_branch,
                                day,
                                pickup_from.strftime("%H:%M"),
                                pickup_latest.strftime("%H:%M"),
                            )
                        )

                    # Else different pickup day
                    else:
                        text = "{} new {} at {} {}, pickup between {} and {}".format(
                            items_available,
                            item_name,
                            store_name,
                            store_branch,
                            pickup_from.strftime("%d-%m-%Y %H:%M"),
                            pickup_latest.strftime("%d-%m-%Y %H:%M"),
                        )

                    logging.info(text)
                    notifier.send_notification(text)

        # Update or create favorite store
        db.update_create_favorite_store(
            user_id,
            store_id,
            item_id,
            items_available,
        )
