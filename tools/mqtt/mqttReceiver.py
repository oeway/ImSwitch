import paho.mqtt.client as mqtt

def on_connect(client, userdata, flags, rc):
    print("Connected with result code " + str(rc))
    client.subscribe("test/topic")

def on_message(client, userdata, msg):
    print(f"Received message: {msg.payload.decode()} on topic {msg.topic}")

# Create a client instance
client = mqtt.Client()

# Assign callback functions
client.on_connect = on_connect
client.on_message = on_message

# Connect to an MQTT broker
client.connect("localhost", 1883, 60)

# Run the loop
client.loop_forever()
