import time

import requests

ROOM_ID = "6d8be848-6c8b-4a74-ac13-8826d1ca5868"
BASE_URL = "http://localhost:8080"


def check_messages():
    try:
        response = requests.post(
            f"{BASE_URL}/execute",
            json={"command": "mcp_quorus_check_messages", "params": {}},
        )
        return response.json().get("output", "")
    except Exception as e:
        print(f"Error checking messages: {e}")
        return ""


def send_message(content):
    try:
        requests.post(
            f"{BASE_URL}/execute",
            json={
                "command": "mcp_quorus_send_room_message",
                "params": {"room_id": ROOM_ID, "content": content},
            },
        )
    except Exception as e:
        print(f"Error sending message: {e}")


# This is a simplified logic. In reality, the agent would use its LLM capabilities to analyze
# the output of check_messages and decide whether to send a message based on the context.
def run():
    print("Autonomous loop started.")
    while True:
        messages = check_messages()
        # Logic to decide if we need to respond
        # For simplicity, if we see new team activity, we confirm status
        if "arav" in messages and "TASK" in messages:
            # Placeholder for actual intelligent response logic
            pass

        time.sleep(90)


if __name__ == "__main__":
    run()
