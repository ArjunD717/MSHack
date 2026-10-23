from openai import AzureOpenAI

endpoint = "https://app-uoaihack2ix6ak.azurewebsites.net"
model_name = "text-embedding-3-large"

api_key = "<your api key>"
api_version = "2024-12-01-preview"

client = AzureOpenAI(
    api_version=api_version,
    azure_endpoint=endpoint,
    api_key=api_key,
)

response = client.embeddings.create(
    input = ["first phrase","second phrase","third phrase"],
    model = model_name
)

print(response.model_dump_json(indent=2))

for item in response.data:
    length = len(item.embedding)
    print(
        f"data[{item.index}]: length={length}, "
        f"[{item.embedding[0]}, {item.embedding[1]}, "
        f"..., {item.embedding[length-2]}, {item.embedding[length-1]}]"
    )
print(response.usage)