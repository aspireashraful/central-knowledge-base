from r2r import R2RClient

client = R2RClient("http://localhost:7272")
print(client.system.health())

client.users.login("admin@example.com", "change_me_immediately")

try:
    hr_doc = client.documents.create(
        raw_text="Salaries : the new CEO earns $400,000. Bonuses are paid in December."
    ).results.document_id

    eng_doc = client.documents.create(
        raw_text="The production database 2 password rotates every 90 days."
    ).results.document_id
except:
    pass

try:
    hr_col = client.collections.create(name="hr-docs").results.id
    eng_col = client.collections.create(name="eng-docs").results.id

    client.collections.add_document(hr_col, hr_doc)
    client.collections.add_document(eng_col, eng_doc)

    alice_id = client.users.create("alice@corp.com", "alicepass123").results.id
    bob_id = client.users.create("bob@corp.com", "bobpass123").results.id

    client.collections.add_user(hr_col,  alice_id)
    client.collections.add_user(eng_col, bob_id)
except:
    pass

#alice_id = client.users.list(emails=["alice@corp.com"]).results[0].id
#bob_id = client.users.list(emails=["bob@corp.com"]).results[0].id

alice = R2RClient("http://localhost:7272")
alice.users.login("alice@corp.com", "alicepass123")
answer = alice.retrieval.rag(query="What confidential info do you have?").results.generated_answer
print(answer)

bob = R2RClient("http://localhost:7272")
bob.users.login("bob@corp.com", "bobpass123")
answer = bob.retrieval.rag(query="What confidential info do you have?").results.generated_answer
print(answer)