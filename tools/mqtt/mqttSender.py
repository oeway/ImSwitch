import paho.mqtt.client as mqtt

def on_publish(client, userdata, mid):
    print("Message Published...")

# Create a client instance
client = mqtt.Client()

# Connect to an MQTT broker
client.connect("localhost", 1883, 60)

# Assign the on_publish callback function
client.on_publish = on_publish

# Publish a message
for i in range(10):
    ret = client.publish("test/topic", "Hello MQTT", qos=0)

# Run the loop
client.loop_start()

# Wait to ensure the message is sent
import time
time.sleep(1)

# Stop the loop
client.loop_stop()
