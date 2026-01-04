import os
import random
import time

import cv2
import pytesseract
from atproto import Client, models
from dotenv import load_dotenv

import libvirt_interface
import utils
from constants import *

load_dotenv()

client = Client(os.getenv("BSKY_PDS"))
client.login(os.getenv("BSKY_HANDLE"), os.getenv("BSKY_PASSWORD"))


def handle_mentions(original_post_uri, original_post_cid):
    valid_responses = []
    start_time = time.time()
    end_time = 0
    processed_uris = set()
    timer_inactive = True

    while timer_inactive or end_time - time.time() >= 0:
        try:
            response = client.app.bsky.notification.list_notifications(
                params={"limit": 50}
            )
            for notification in response.notifications:
                if notification.reason == "reply":
                    if notification.record.reply.parent.uri != original_post_uri:
                        continue
                    if notification.uri in processed_uris:
                        continue  # Skip this mention if it has already been processed
                    plain_text = notification.record.text
                    # really basic keyword filter that blocks people from just rm -rf'ing the whole disk or portscanning
                    # this is very, very, very easy to get around
                    # we're basically assuming that people won't try very hard to do anything bad
                    # if that's something you're considering... please don't waste your time wasting mine.
                    if any(
                        keyword in plain_text
                        for keyword in [
                            "!cmd",
                            "!enter",
                            "!ctrl",
                            "!type",
                            "!key",
                            "!tty",
                        ]
                    ) and not any(
                        keyword in plain_text
                        for keyword in [
                            "masscan",
                            "rm -r /*",
                            "rm -rf /*",
                            "rm -fr /*",
                            "--no-preserve-root",
                        ]
                    ):
                        if timer_inactive:
                            timer_inactive = False
                            end_time = time.time() + TIME_DELAY_AFTER_FIRST_COMMENT

                        reply_ref = models.AppBskyFeedPost.ReplyRef(
                            parent=models.ComAtprotoRepoStrongRef.Main(
                                uri=notification.uri, cid=notification.cid
                            ),
                            root=models.ComAtprotoRepoStrongRef.Main(
                                uri=original_post_uri, cid=original_post_cid
                            ),
                        )

                        reply_text = f"Like this post to vote for the above command!\nRunning the most liked command in {utils.format_seconds(int(end_time - time.time() + 1))}."
                        response_post = client.send_post(
                            text=reply_text, reply_to=reply_ref
                        )

                        # Store the response details
                        valid_responses.append(
                            {
                                "response_uri": response_post.uri,
                                "response_cid": response_post.cid,
                                "original_comment_uri": notification.uri,
                                "original_comment_cid": notification.cid,
                                "username": notification.author.handle,
                                "like_count": 0,
                            }
                        )
                        processed_uris.add(notification.uri)
        except Exception as err:
            print(f"error grabbing notifs: {err}")
        time.sleep(5)

    return valid_responses


def post_image_and_log_response():
    # Post the image
    media_filename = f"./{libvirt_interface.grab_screenshot()}"
    print(f"screenshotted {media_filename}")
    img = cv2.imread(media_filename)
    ocr = "[automatic] OCR of the screenshot: \n" + pytesseract.image_to_string(img)

    with open(media_filename, "rb") as f:
        img_data = f.read()

    post = client.send_image(
        text="",
        image=img_data,
        image_alt=ocr[:999],
    )

    valid_responses = handle_mentions(post.uri, post.cid)
    for response in valid_responses:
        try:
            likes_response = client.app.bsky.feed.get_likes(
                params={"uri": response["response_uri"]}
            )
            response["like_count"] = len(likes_response.likes)
            print(
                f"re-fetched like count for {response['username']}: {response['like_count']}"
            )
        except Exception as e:
            print(f"Error fetching likes: {e}")

    # Find the most favorited response
    if valid_responses:
        max_likes = max(response["like_count"] for response in valid_responses)
        top_responses = [
            response
            for response in valid_responses
            if response["like_count"] == max_likes
        ]
        print(f"top responses: {top_responses}")
        most_liked_response = random.choice(top_responses)

        original_comment_response = client.app.bsky.feed.get_posts(
            params={"uris": [most_liked_response["original_comment_uri"]]}
        )
        original_comment = original_comment_response.posts[0]
        plain_text = original_comment.record.text

        print(f"selected original comment content: {plain_text}")

        plain_text = "!" + "!".join(plain_text.split("!", 1)[1:])
        print(f"Most favorited response: {plain_text}")

        author_handle = most_liked_response["username"]
        client.send_post(
            text=f"Selected response:\n{plain_text}\nposted by @{author_handle}\nPosting a screenshot soon!"
        )
        commands = plain_text.split("\n")
        for command in commands:
            command = command.strip()
            if command.startswith("!ctrl"):
                print("CONTROL")
                print(command[6])  # Print the first character after '!ctrl'
                libvirt_interface.key(f"ctrl {command[6]}")
            elif command.startswith("!enter"):
                print("ENTER")
                libvirt_interface.key("enter")
            elif command.startswith("!cmd"):
                print("COMMAND")
                print(command[5:])
                libvirt_interface.type_text(command[5:])
                libvirt_interface.key("enter")
            elif command.startswith("!type"):
                print("TYPE")
                print(command[6:])
                libvirt_interface.type_text(command[6:])
            elif plain_text.startswith("!tty"):
                print("TTY")
                libvirt_interface.key(f"ctrl alt f{command[4]}")
            elif plain_text.startswith("!key"):
                print("KEY")
                print(command[5:])
                libvirt_interface.key(command[5:])

            else:
                print(f"Most favorited response: {plain_text}")
            time.sleep(5)

    else:
        print("No responses received.")


# Run the bot
if __name__ == "__main__":
    while True:
        libvirt_interface.start_vm_if_not_running()
        post_image_and_log_response()
        time.sleep(TIME_DELAY_AFTER_RUNNING_COMMAND)
