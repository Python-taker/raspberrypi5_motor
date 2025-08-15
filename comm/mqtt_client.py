import paho.mqtt.client as mqtt

class MQTTClient:
    def __init__(self, broker_host, broker_port, subscribe_topics=None, publish_topics=None):
        self.client = mqtt.Client()
        self.broker_host = broker_host
        self.broker_port = broker_port
        self.subscribe_topics = subscribe_topics if subscribe_topics else []
        self.publish_topics = publish_topics if publish_topics else []

    def on_connect(self, client, userdata, flags, rc):
        print(f"[MQTT] Connected to broker ({self.broker_host}:{self.broker_port})")
        if self.subscribe_topics:
            client.subscribe(self.subscribe_topics)
            print(f"[MQTT] Subscribed to: {[t[0] for t in self.subscribe_topics]}")

    def on_message(self, client, userdata, msg):
        print(f"[MQTT] SUB: {msg.topic} Payload: {msg.payload.decode()}")

    def connect(self):
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.client.connect(self.broker_host, self.broker_port)
        self.client.loop_start()

    def publish(self, topic, payload, qos=0):
        topics_allowed = [t[0] for t in self.publish_topics]
        if topic in topics_allowed:
            print(f"[MQTT] PUB: {topic} (QoS={qos}): {payload}")
            self.client.publish(topic, payload, qos=qos)
        else:
            print(f"[MQTT][Warning] {topic} is not in publish_topics list. Publishing anyway.")
            self.client.publish(topic, payload, qos=qos)
