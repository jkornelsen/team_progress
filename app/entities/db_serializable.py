from flask import g

class DbSerializable:
    """
    Superclass with methods for serializing to database along with some other
    things that entities have in common.
    """
    @classmethod
    def get_by_id(cls, id_to_get):
        id_to_get = int(id_to_get)
        return next(
            (instance for instance in cls.instances
            if instance.id == id_to_get), None)

    @classmethod
    def list_from_json(cls, json_data, id_references=None):
        cls.instances.clear()
        for entity_data in json_data:
            cls.from_json(entity_data, id_references)
        cls.last_id = max(
            (instance.id for instance in cls.instances), default=0)
        return cls.instances

    @classmethod
    def get_collection(cls):
        return g.db[cls.__name__.lower()]

    @classmethod
    def remove_from_db(cls, doc_id):
        collection = cls.get_collection()
        collection.delete_one({'game_token': g.game_token, 'id': int(doc_id)})

    def to_db(self):
        collection = self.__class__.get_collection()
        doc = self.to_json()
        doc['game_token'] = g.game_token
        if collection.find_one({'game_token': g.game_token, 'id': self.id}):
            print(f"Updating document for {self.__class__.__name__} with id {self.id}")
            collection.update_one({'game_token': g.game_token, 'id': self.id}, {'$set': doc})
            # or replace_one()?
        else:
            print(f"Inserting new document for {self.__class__.__name__} with id {self.id}")
            collection.insert_one(doc)

    @classmethod
    def list_to_db(cls):
        collection = cls.get_collection()
        existing_ids = set(
            str(doc['id'])
            for doc in collection.find({'game_token': g.game_token}))
        for instance in cls.instances:
            instance.to_db()
        for doc_id in existing_ids:
            if doc_id not in (str(instance.id) for instance in cls.instances):
                print(f"Removing document with id {doc_id}")
                cls.remove_from_db(doc_id)

    @classmethod
    def list_from_db(cls, id_references=None):
        print(f"{cls.__name__}.list_from_db()")
        cls.instances.clear()
        collection = cls.get_collection()
        docs = collection.find({'game_token': g.game_token})
        instances = [cls.from_json(doc, id_references) for doc in docs]
        cls.last_id = max(
            (instance.id for instance in cls.instances), default=0)
        return instances


