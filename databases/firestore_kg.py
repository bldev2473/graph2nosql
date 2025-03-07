"""Firestore database operations implementation"""

from typing import List

import firebase_admin  # type: ignore
from firebase_admin import firestore
from google.cloud.firestore_v1.base_vector_query import DistanceMeasure
from google.cloud.firestore_v1.vector import Vector
import google.auth

import networkx as nx  # type: ignore

from datamodel.data_model import NodeData, EdgeData, CommunityData
from base.operations import NoSQLKnowledgeGraph


class FirestoreKG(NoSQLKnowledgeGraph):
    """Firestore database operations implementation class"""

    def __init__(
        self,
        firestore_client,
        node_collection_id: str,
        edges_collection_id: str,
        community_collection_id: str
    ) -> None:
        """
        Initializes the FirestoreKG object.

        Args:
            project_id (str): The Google Cloud project ID.
            database_id (str): The ID of the Firestore database.
            collection_name (str): The name of the collection to store the KG.
        """
        super().__init__()

        self.db = firestore_client
        self.node_coll_id = node_collection_id
        self.edges_coll_id = edges_collection_id
        self.community_coll_id = community_collection_id

    @classmethod
    def from_app(
        cls,
        gcp_project_id: str,
        gcp_credential_file: str,
        firestore_db_id: str,
        node_collection_id: str,
        edges_collection_id: str,
        community_collection_id: str
    ):
        """
        Args:
            project_id (str): The Google Cloud project ID.
            database_id (str): The ID of the Firestore database.
            collection_name (str): The name of the collection to store the KG.
        """
        if not firebase_admin._apps:
            credentials = firebase_admin.credentials.Certificate(
                gcp_credential_file
            )
            app = firebase_admin.initialize_app(credentials)
            
        credentials, gcp_project_id = google.auth.load_credentials_from_file(
            gcp_credential_file
        )
        
        db = firestore.Client(
            project=gcp_project_id,  # type: ignore
            credentials=credentials,
            database=firestore_db_id,
        )

        return cls(
            firestore_client=db,
            node_collection_id=node_collection_id,
            edges_collection_id=edges_collection_id,
            community_collection_id=community_collection_id,
        )
    
    def add_node(self, node_uid: str, node_data: NodeData) -> None:
        """Adds an node to the knowledge graph."""
        doc_ref = self.db.collection(self.node_coll_id).document(node_uid)

        # Check if a node with the same node_uid already exists
        if doc_ref.get().exists:
            raise ValueError(
                f"Error: Node with node_uid '{node_uid}' already exists.")

        # block NodeData if edge info is included
        if node_data.edges_to or node_data.edges_from:
            raise ValueError(
                f"""Error: NodeData cannot be initiated with edges_to or edges_from. Please add edges separately.""")

        # Convert NodeData to a dictionary for Firestore storage
        try:
            node_data_dict = node_data.__dict__
        except TypeError as e:
            raise ValueError(
                f"Error: Provided node_data for node_uid '{node_uid}' cannot be converted to a dictionary. Details: {e}"
            ) from e

        # Set the document ID to match the node_uid
        try:
            doc_ref.set(node_data_dict)
        except ValueError as e:
            raise ValueError(
                f"Error: Could not add node with node_uid '{node_uid}' to Firestore. Details: {e}"
            ) from e

        # Update references in other nodes
        for other_node_uid in node_data.edges_to:
            try:
                other_node_data = self.get_node(other_node_uid)
                other_node_data.edges_from = list(set(other_node_data.edges_from) | {
                                                  node_uid})  # Add to edges_from
                self.update_node(other_node_uid, other_node_data)
            except KeyError:
                # If the other node doesn't exist, just continue
                continue

        for other_node_uid in node_data.edges_from:
            try:
                other_node_data = self.get_node(other_node_uid)
                other_node_data.edges_to = list(set(other_node_data.edges_from) | {
                                                node_uid})  # Add to edges_to
                self.update_node(other_node_uid, other_node_data)
            except KeyError:
                # If the other node doesn't exist, just continue
                continue

    def get_node(self, node_uid: str) -> NodeData:
        """Retrieves an node from the knowledge graph."""
        doc_ref = self.db.collection(self.node_coll_id).document(node_uid)
        doc_snapshot = doc_ref.get()

        if doc_snapshot.exists:
            try:
                node_data = NodeData(**doc_snapshot.to_dict())
                return node_data
            except TypeError as e:
                raise ValueError(
                    f"Error: Data fetched for node_uid '{node_uid}' does not match the NodeData format. Details: {e}"
                ) from e
        else:
            raise KeyError(f"Error: No node found with node_uid: {node_uid}")

    def update_node(self, node_uid: str, node_data: NodeData) -> None:
        """Updates an existing node in the knowledge graph."""
        doc_ref = self.db.collection(self.node_coll_id).document(node_uid)

        # Check if the node exists
        if not doc_ref.get().exists:
            raise KeyError(
                f"Error: Node with node_uid '{node_uid}' does not exist.")

        # Convert NodeData to a dictionary for Firestore storage
        try:
            node_data_dict = node_data.__dict__
        except TypeError as e:
            raise ValueError(
                f"Error: Provided node_data for node_uid '{node_uid}' cannot be converted to a dictionary. Details: {e}"
            ) from e

        # Update the document
        try:
            doc_ref.update(node_data_dict)
        except ValueError as e:
            raise ValueError(
                f"Error: Could not update node with node_uid '{node_uid}' in Firestore. Details: {e}"
            ) from e

    def remove_node(self, node_uid: str) -> None:
        """
        Removes an node from the knowledge graph.
        Also removed all edges to and from the node to be removed from all other nodes.
        """
        doc_ref = self.db.collection(self.node_coll_id).document(node_uid)

        # TODO: Update edge collection on edge removal.

        # Check if the node exists
        if not doc_ref.get().exists:
            raise KeyError(
                f"Error: Node with node_uid '{node_uid}' does not exist.")

        # 1. Get the node data to find its connections
        node_data = self.get_node(node_uid)

        # 2. Remove connections TO this node from other nodes
        for other_node_uid in node_data.edges_from:
            try:
                other_node_data = self.get_node(other_node_uid)
                other_node_data.edges_to = list(
                    edge for edge in other_node_data.edges_to if edge != node_uid
                )
                self.update_node(other_node_uid, other_node_data)
            except KeyError:
                # If the other node doesn't exist, just continue
                continue

        # 3. Remove connections FROM this node to other nodes
        for other_node_uid in node_data.edges_to:
            try:
                other_node_data = self.get_node(other_node_uid)
                other_node_data.edges_from = list(
                    edge for edge in other_node_data.edges_from if edge != node_uid
                )
                self.update_node(other_node_uid, other_node_data)
            except KeyError:
                # If the other node doesn't exist, just continue
                continue

        # 4. Finally, remove the node itself
        doc_ref.delete()

    def add_edge(self, edge_data: EdgeData) -> None:
        """
        Adds an edge (relationship) between two entities in the knowledge graph.

        Args:
            source_uid (str): The UID of the source node.
            target_uid (str): The UID of the target node.
            edge_data (EdgeData): The edge data to be added.
            directed (bool, optional): Whether the edge is directed. Defaults to True.
        """

        # Check if source and target nodes exist
        if not self.get_node(edge_data.source_uid):
            raise KeyError(
                f"Error: Source node with node_uid '{edge_data.source_uid}' does not exist.")
        if not self.get_node(edge_data.target_uid):
            raise KeyError(
                f"Error: Target node with node_uid '{edge_data.target_uid}' does not exist.")

        # Type checking for edge_data
        if not isinstance(edge_data, EdgeData):
            raise TypeError(
                f"Error: edge_data must be of type EdgeData, not {type(edge_data)}")

        edge_uid = self._generate_edge_uid(
            source_uid=edge_data.source_uid, target_uid=edge_data.target_uid)

        try:
            source_node_data = self.get_node(edge_data.source_uid)
            target_node_data = self.get_node(edge_data.target_uid)

            source_node_data.edges_to = list(
                set(source_node_data.edges_to) | {edge_data.target_uid})
            self.update_node(edge_data.source_uid, source_node_data)

            # Add the edge to the target node's edges_from
            target_node_data.edges_from = list(
                set(target_node_data.edges_from) | {edge_data.source_uid})
            self.update_node(edge_data.target_uid, target_node_data)

            # Add the edge to the edges collection
            self._update_egde_coll(edge_uid=edge_uid,
                                   target_uid=edge_data.target_uid,
                                   source_uid=edge_data.source_uid,
                                   description=edge_data.description,
                                   directed=edge_data.directed)

            if not edge_data.directed:  # If undirected, add the reverse edge as well
                target_node_data.edges_to = list(
                    set(target_node_data.edges_to) | {edge_data.source_uid})
                self.update_node(edge_data.target_uid, target_node_data)

                # Since it's undirected, also add source_uid to target_node_data.edges_from
                source_node_data.edges_from = list(
                    set(source_node_data.edges_from) | {edge_data.target_uid})
                self.update_node(edge_data.source_uid, source_node_data)

                # Add the reverse edge to the edges collection
                reverse_edge_uid = self._generate_edge_uid(source_uid=edge_data.target_uid,
                                                           target_uid=edge_data.source_uid)
                self._update_egde_coll(edge_uid=reverse_edge_uid,
                                       target_uid=edge_data.source_uid,
                                       source_uid=edge_data.target_uid,
                                       description=edge_data.description,
                                       directed=edge_data.directed)

        except ValueError as e:
            raise ValueError(
                f"Error: Could not add edge from '{edge_data.source_uid}' to '{edge_data.target_uid}'. Details: {e}"
            ) from e

    def get_edge(self, source_uid: str, target_uid: str) -> EdgeData:
        """Retrieves an edge between two entities from the edges collection."""
        edge_uid = self._generate_edge_uid(source_uid, target_uid)
        edge_doc_ref = self.db.collection(
            self.edges_coll_id).document(edge_uid)
        doc_snapshot = edge_doc_ref.get()

        if doc_snapshot.exists:
            try:
                edge_data = EdgeData(**doc_snapshot.to_dict())
                return edge_data
            except TypeError as e:
                raise ValueError(
                    f"Error: Data fetched for edge_uid '{edge_uid}' does not match the EdgeData format. Details: {e}"
                ) from e
        else:
            raise KeyError(f"Error: No edge found with edge_uid: {edge_uid}")

    def update_edge(self, edge_data: EdgeData) -> None:
        """Updates an existing edge in the knowledge graph."""

        # 1. Validate input and check if the edge exists
        if not isinstance(edge_data, EdgeData):
            raise TypeError(
                f"Error: edge_data must be of type EdgeData, not {type(edge_data)}")

        edge_uid = self._generate_edge_uid(
            edge_data.source_uid, edge_data.target_uid)

        if not self.db.collection(self.edges_coll_id).document(edge_uid).get().exists:
            raise KeyError(
                f"Error: Edge with edge_uid '{edge_uid}' does not exist.")

        # 2. Update the edge document in the EDGES collection
        try:
            self._update_egde_coll(
                edge_uid=edge_uid,
                target_uid=edge_data.target_uid,
                source_uid=edge_data.source_uid,
                description=edge_data.description,
                directed=edge_data.directed
            )
        except Exception as e:
            raise Exception(
                f"Error updating edge in edges collection: {e}") from e

        # 3. Update edge references in the NODES collection
        try:
            # 3a. Update source node
            source_node_data = self.get_node(edge_data.source_uid)
            # Ensure the target_uid is present in edges_to
            if edge_data.target_uid not in source_node_data.edges_to:
                source_node_data.edges_to = list(
                    set(source_node_data.edges_to) | {edge_data.target_uid})
                self.update_node(edge_data.source_uid, source_node_data)

            # 3b. Update target node
            target_node_data = self.get_node(edge_data.target_uid)
            # Ensure the source_uid is present in edges_from
            if edge_data.source_uid not in target_node_data.edges_from:
                target_node_data.edges_from = list(
                    set(target_node_data.edges_from) | {edge_data.source_uid})
                self.update_node(edge_data.target_uid, target_node_data)

        except Exception as e:
            raise Exception(
                f"Error updating edge references in nodes: {e}") from e

    def _delete_from_edge_coll(self, edge_uid: str) -> None:
        """Method to delete record from edge collection of given kg store"""
        edge_doc_ref = self.db.collection(
            self.edges_coll_id).document(edge_uid)
        edge_doc_ref.delete()

    def remove_edge(self, source_uid: str, target_uid: str) -> None:
        """Removes an edge between two entities."""

        # Get involved edge and node data
        try:
            edge_data = self.get_edge(
                source_uid=source_uid, target_uid=target_uid)
        except Exception as e:
            raise Exception(f"Error getting edge: {e}") from e

        try:
            source_node_data = self.get_node(node_uid=source_uid)
        except Exception as e:
            raise Exception(f"Error getting source node: {e}") from e

        try:
            target_node_data = self.get_node(node_uid=target_uid)
        except Exception as e:
            raise Exception(f"Error getting target node: {e}") from e

        # remove target_uid from from source -> target
        try:
            source_node_data.edges_to.remove(target_uid)
            self.update_node(source_uid, source_node_data)
        except ValueError as e:
            raise ValueError(
                f"Error: Target node not in source's edges_to: {e}")

        # remove source_uid from target <- source
        try:
            target_node_data.edges_from.remove(source_uid)
            self.update_node(target_uid, target_node_data)
        except ValueError as e:
            raise ValueError(
                f"Error: Source node not in target's edges_to: {e}")

        # Remove the edge from the edges collection
        edge_uid = self._generate_edge_uid(source_uid, target_uid)
        self._delete_from_edge_coll(edge_uid=edge_uid)

        # remove the opposite direction if edge undirected
        if not edge_data.directed:
            # remove target_uid from source <- target
            try:
                source_node_data.edges_from.remove(target_uid)
                self.update_node(source_uid, source_node_data)
            except ValueError as e:
                raise ValueError(
                    f"Error: Target node not in source's edges_to: {e}")

            # remove source_uid from target -> source
            try:
                target_node_data.edges_to.remove(source_uid)
                self.update_node(target_uid, target_node_data)
            except ValueError as e:
                raise ValueError(
                    f"Error: Source node not in target's edges_to: {e}")

            # Remove the edge from the edges collection
            reverse_edge_uid = self._generate_edge_uid(target_uid, source_uid)
            self._delete_from_edge_coll(edge_uid=reverse_edge_uid)
        else:
            pass

    def build_networkx(self):
        """Get the NetworkX representation of the full graph."""

        graph = nx.Graph()  # Initialize an undirected NetworkX graph

        # 1. Add Nodes to the NetworkX Graph
        nodes_ref = self.db.collection(self.node_coll_id).stream()
        for doc in nodes_ref:
            node_data = doc.to_dict()
            graph.add_node(doc.id, **node_data)

        # 2. Add Edges to the NetworkX Graph
        edges_ref = self.db.collection(self.edges_coll_id).stream()
        for doc in edges_ref:
            edge_data = doc.to_dict()
            source_uid = edge_data['source_uid']
            target_uid = edge_data['target_uid']
            # Consider adding edge attributes if needed (e.g., 'description')
            graph.add_edge(source_uid, target_uid)

        self.networkx = graph

    def get_community(self, community_id: str) -> CommunityData:
        """Retrieves the community report for a given community id."""
        doc_ref = self.db.collection(
            self.community_coll_id).document(community_id)
        doc_snapshot = doc_ref.get()

        if doc_snapshot.exists:
            try:
                community_data = CommunityData(**doc_snapshot.to_dict())
                return community_data
            except TypeError as e:
                raise ValueError(
                    f"Error: Data fetched for community_id '{community_id}' does not match the CommunityData format. Details: {e}"
                ) from e
        else:
            raise KeyError(
                f"Error: No community found with community_id: {community_id}")

    def list_communities(self) -> List[CommunityData]:
        """Lists all communities for the given network."""
        docs = self.db.collection(self.community_coll_id).stream()
        return [CommunityData.__from_dict__(doc.to_dict()) for doc in docs]

    def _update_egde_coll(self, edge_uid: str, source_uid: str, target_uid: str, description: str, directed: bool) -> None:
        """Update edge record in the edges collection."""
        edge_doc_ref = self.db.collection(
            self.edges_coll_id).document(edge_uid)
        edge_data_dict = {
            "edge_uid": edge_uid,
            "source_uid": source_uid,
            "target_uid": target_uid,
            "description": description,
            "directed": directed
        }
        edge_doc_ref.set(edge_data_dict)

    def store_community(self, community: CommunityData) -> None:
        """Takes valid graph community data and upserts the database with it.
        https://www.nature.com/articles/s41598-019-41695-z
        """
        # Convert CommunityData to a dictionary for Firestore storage
        try:
            community_data_dict = community.__dict__
        except TypeError as e:
            raise ValueError(
                f"Error: Provided community data for community '{community.title}' cannot be converted to a dictionary. Details: {e}"
            ) from e

        # Get a reference to the document
        doc_ref = self.db.collection(
            self.community_coll_id).document(community.title)

        # Use set with merge=True to upsert the document
        try:
            doc_ref.set(community_data_dict, merge=True)
        except Exception as e:
            raise Exception(f"Error storing community data: {e}") from e

    def _generate_edge_uid(self, source_uid: str, target_uid: str):
        return f"{source_uid}_to_{target_uid}"

    def node_exist(self, node_uid: str) -> bool:
        """Checks for node existence and returns boolean"""
        doc_ref = self.db.collection(self.node_coll_id).document(node_uid)
        doc_snapshot = doc_ref.get()

        if doc_snapshot.exists:
            return True
        else:
            return False

    def edge_exist(self, source_uid: str, target_uid: str) -> bool:
        """Checks for edge existence and returns boolean"""
        edge_uid = self._generate_edge_uid(
            source_uid=source_uid, target_uid=target_uid)
        doc_ref = self.db.collection(self.edges_coll_id).document(edge_uid)
        doc_snapshot = doc_ref.get()

        if doc_snapshot.exists:
            return True
        else:
            return False

    def get_nearest_neighbors(self, query_vec: list[float]) -> list:
        """
        Implements nearest neighbor search based on Firestore embedding index:
        https://firebase.google.com/docs/firestore/vector-search
        """

        collection = self.db.collection(self.node_coll_id)

        # Requires vector index
        nn = collection.find_nearest(
            vector_field="embedding",
            query_vector=Vector(query_vec),
            distance_measure=DistanceMeasure.EUCLIDEAN,
            limit=10).get()
        return [n.to_dict() for n in nn]

    def clean_zerodegree_nodes(self) -> None:
        """Removes all nodes with degree 0."""
        nodes_to_remove = []

        # 1. Iterate through all nodes to find those with degree 0
        nodes_ref = self.db.collection(self.node_coll_id).stream()
        for doc in nodes_ref:
            node_data = doc.to_dict()
            if len(node_data.get('edges_to', [])) + len(node_data.get('edges_from', [])) == 0:
                nodes_to_remove.append(doc.id)

        # 2. Remove the identified nodes
        for node_uid in nodes_to_remove:
            self.remove_node(node_uid)
        return None

    def flush_kg(self) -> None:
        """Method to wipe the complete datastore of the knowledge graph"""
        for collection_id in [self.node_coll_id, self.edges_coll_id, self.community_coll_id]:
            docs = self.db.collection(collection_id).stream()
            for doc in docs:
                doc.reference.delete()
        return None


if __name__ == "__main__":
    import os
    from dotenv import dotenv_values

    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    secrets = dotenv_values(".env")

    firestore_credential_file = str(secrets["GCP_CREDENTIAL_FILE"])
    project_id = str(secrets["GCP_PROJECT_ID"])
    database_id = str(secrets["FIRESTORE_DB_ID"])
    node_coll_id = str(secrets["NODE_COLL_ID"])
    edges_coll_id = str(secrets["EDGES_COLL_ID"])
    community_coll_id = str(secrets["COMM_COLL_ID"])

    fskg = FirestoreKG(
        gcp_project_id=project_id,
        gcp_credential_file=firestore_credential_file,
        firestore_db_id=database_id,
        node_collection_id=node_coll_id,
        edges_collection_id=edges_coll_id,
        community_collection_id=community_coll_id
    )

    node = fskg.get_node(node_uid="2022 IRANIAN PROTESTS")

    nn = fskg.get_nearest_neighbors(node.embedding)

    for n in nn:
        print(n["node_uid"])

    print("Hello World!")
    print("")
