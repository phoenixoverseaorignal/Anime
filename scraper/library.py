"""

This file will handle the saving and extraction of metadata about downloaded files.

Example: {file_name: {total_size: int, location: str}}

"""

from abc import ABC, abstractmethod
from typing import Dict, List, Any
import json


class Library(ABC):
    data: List[Dict[str, Dict[str, Any]]] = []

    @classmethod
    @abstractmethod
    def get_all(cls) -> List[Dict[str, Dict[str, Any]]]:
        ...

    @classmethod
    @abstractmethod
    def save(cls) -> bool:
        ...

    @classmethod
    @abstractmethod
    def add(cls, _data: Dict[str, Dict[str, Any]]) -> None:
        ...

    @classmethod
    @abstractmethod
    def load_data(cls):
        ...


class JsonLibrary(Library):
    file_location: str = "library.json"

    @classmethod
    def get_all(cls) -> List[Dict[str, Dict[str, Any]]]:
        return cls.data

    @classmethod
    def save(cls) -> bool:
        if cls.data:
            print("saving data")
            with open(cls.file_location, 'w') as j_file:
                j_file.write(json.dumps(cls.data, indent=4))
            print("Data successfully saved")

            cls.data = []  # reset the in-memory data
        return True

    @classmethod
    def add(cls, _data: Dict[str, Dict[str, Any]]) -> None:
        cls.data.append(_data)

    @classmethod
    def load_data(cls):
        print("loading data")
        with open(cls.file_location, 'r') as j_file:
            cls.data = json.load(j_file)