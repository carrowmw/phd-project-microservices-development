import os
import random
from datetime import datetime, timedelta
import numpy as np
import pandas as pd
from phd_package.pipeline import Pipeline
from .utils.transformation_helper import unbatch_dataloaders_to_numpy
from .utils.error_helper import handle_data_errors
from ..config.paths import *
from ..utils.data_helper import *
from ..utils.config_helper import (
    get_datetime_column,
    get_n_days,
    get_anomaly_std,
)


class CustomDashboardData:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(CustomDashboardData, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        print("CustomDashboardData.__init__() started...")
        self.sensor_metrics = {"completeness": {}, "freshness": {}, "anomalies": {}}
        self.active_sensors = []
        self._load_or_run_pipeline()
        self._initialize_attributes()
        self._initialized = True
        self._debug_print_data()
        # self._debug_print_sensor_info()
        print("CustomDashboardData.__init__() completed.\n")

    def _debug_print_data(self):
        print("Debug: Sensor Info")
        print(self.get_sensor_info())
        print("\nDebug: Completeness Metrics")
        print(self.get_completeness_metrics())
        print("\nDebug: Freshness Metrics")
        print(self.get_freshness_metrics())

    def _debug_print_sensor_info(self):
        print("Active sensors:", self.active_sensors)
        print(
            "Sensors in completeness metrics:",
            list(self.sensor_metrics["completeness"].keys()),
        )
        print("Sensors in get_sensor_info():", self.get_sensor_info()[2])

        missing_in_metrics = set(self.active_sensors) - set(
            self.sensor_metrics["completeness"].keys()
        )
        missing_in_info = set(self.active_sensors) - set(self.get_sensor_info()[2])

        if missing_in_metrics:
            print("Sensors missing in metrics:", missing_in_metrics)
        if missing_in_info:
            print("Sensors missing in get_sensor_info():", missing_in_info)

    @handle_data_errors(default_return=lambda: None)
    def _load_or_run_pipeline(self):
        try:
            self.data = load_raw_data()
            self.preprocessed_data = load_preprocessed_data()
            self.engineered_data = load_engineered_data()
            self.dataloader = load_dataloader()
            self.trained_models = load_trained_models()
            self.test_metrics = load_test_metrics()

            # Check if any of the required data is empty
            if not all([self.data, self.preprocessed_data, self.engineered_data, self.dataloader, self.trained_models, self.test_metrics]):
                raise ValueError("One or more of the required datasets is empty")

            return True # Indicates success
        except Exception as e:
            print(f"Error in _load_or_run_pipeline: {str(e)}")
            # If loading fails, initialize with empty data
            self.data = []
            self.preprocessed_data = []
            self.engineered_data = []
            self.dataloader = []
            self.trained_models = []
            self.test_metrics = []
            return False # Indicates failure

    @handle_data_errors(default_return=lambda: None)
    def _initialize_attributes(self):
        try:
            self.latest_data = [(tuple[0], tuple[1][-500:]) for tuple in self.data] if self.data else []
            self.sensors = load_sensor_list()
            self.active_sensors = [tuple[0] for tuple in self.data] if self.data else []
            self.trainable_sensors = [tuple[0] for tuple in self.dataloader] if self.data else []
            self.datetime_column = get_datetime_column()
            self.n_days = get_n_days()
            self.anomaly_std = get_anomaly_std()

            # compute metrics even if data is empty
            self._compute_completeness_metrics()
            self._compute_freshness_metrics()
            return True # Indicates successful initialisation
        except Exception as e:
            print(f"Error in initialised attributes: {str(e)}")
            return False # Indicates failed initialisation


    @handle_data_errors(default_return=lambda: (pd.Series(), pd.Series(), pd.Series()))
    def get_sensor_info(self):
        if not hasattr(self, "sensors") or self.sensors is None:
            return [], [], []
        active_sensor_info = self.sensors[
            self.sensors["Sensor Name"].isin(self.active_sensors)
        ]
        return (
            active_sensor_info["Sensor Centroid Latitude"],
            active_sensor_info["Sensor Centroid Longitude"],
            active_sensor_info["Sensor Name"],
        )

    @handle_data_errors(default_return=lambda: {})
    def get_sensor_metrics(self, metric_type):
        return self.sensor_metrics.get(metric_type, {})

    @handle_data_errors(default_return=lambda: [])
    def _read_or_compute_data(self, file_path, compute_func):
        print(f"Debug: Attempting to read or compute data from {file_path}")
        if os.path.exists(file_path):
            print(f"Debug: Reading data from {file_path}")
            data = load_data(file_path, "app data")
            if data is not None:
                print(f"Debug: Data loaded successfully from {file_path}")
                return data
            else:
                print(f"Debug: Failed to load data from {file_path}")
        else:
            print(f"Debug: File does not exist at {file_path}")
        try:
            data = compute_func()
            if data:
                print(f"Debug: Data computed successfully, saving to {file_path}")
                os.makedirs(os.path.dirname(file_path), exist_ok=True)
                save_data(data, file_path, "app data")
                print(f"Debug: Data saved to {file_path}")
            else:
                print(f"Debug: Compute functions returned no data: {data}")
            return data
        except Exception as e:
            print(f"Error in compute_func: {str(e)}")
            return []

    @handle_data_errors(default_return=pd.DataFrame())
    def get_daily_counts(self):
        file_path = create_file_path(
            get_daily_record_counts_path, pipeline_input_data_filename
        )
        return self._read_or_compute_data(file_path, self._compute_daily_counts)

    @handle_data_errors(default_return=lambda: pd.DataFrame())
    def _compute_daily_counts(self):
        daily_counts = []
        for sensor_name, df in self.data:
            df = (
                df.groupby(df[self.datetime_column].dt.date)
                .size()
                .reset_index(name="Count")
            )
            daily_counts.append((sensor_name, df))
        return daily_counts

    @handle_data_errors(default_return=pd.DataFrame())
    def get_completeness_metrics(self):
        file_path = create_file_path(
            get_completeness_metrics_path, pipeline_input_data_filename
        )
        metrics = self._read_or_compute_data(
            file_path, self._compute_completeness_metrics
        )
        # print(f"Debug: Completeness metrics sensors: {metrics['sensor_name'].tolist()}")
        return metrics

    def _compute_completeness_metrics(self):
        df = pd.DataFrame(
            {
                "sensor_name": [data[0] for data in self.data],
                "length": [len(data[1]) for data in self.data],
            }
        )
        df["float"] = df["length"] / (self.n_days * 96)
        df["norm"] = df["float"] / df["float"].max()
        df["string"] = df["float"].apply(lambda x: f"{round(x * 100, 2)}%")

        # store sensor metrics in a single source of truth
        self.sensor_metrics["completeness"] = {
            sensor: {
                "float": row["float"] if not pd.isna(row["float"]) else 0,
                "norm": row["norm"] if not pd.isna(row["norm"]) else 0,
                "string": row["string"] if not pd.isna(row["string"]) else "No data",
            }
            for sensor, row in df.set_index("sensor_name").iterrows()
        }

        # ensure all active sensors are in the metrics
        for sensor in self.active_sensors:
            if sensor not in self.sensor_metrics["completeness"]:
                self.sensor_metrics["completeness"][sensor] = {
                    "float": 0,
                    "norm": 0,
                    "string": "No data",
                }
        return df

    @handle_data_errors(default_return=pd.DataFrame())
    def get_freshness_metrics(self):
        file_path = create_file_path(
            get_freshness_metrics_path, pipeline_input_data_filename
        )
        metrics = self._read_or_compute_data(file_path, self._compute_freshness_metrics)

        # print(f"Debug: Freshness metrics sensors: {metrics['sensor_name'].tolist()}")
        return metrics

    def _compute_freshness_metrics(self):
        df = pd.DataFrame(
            {
                "sensor_name": [data[0] for data in self.data],
                "most_recent": [
                    data[1][self.datetime_column].max() for data in self.data
                ],
            }
        )
        df["float"] = (
            datetime.now() - df["most_recent"]
        ).dt.total_seconds() / timedelta(days=self.n_days).total_seconds()
        df["norm"] = df["float"] / df["float"].max()
        df["log"] = np.log(df["float"])
        df["norm_log"] = (df["log"] - df["log"].min()) / (
            df["log"].max() - df["log"].min()
        )
        df["string"] = (datetime.now() - df["most_recent"]).apply(
            lambda x: f"{x.days} days\n{x.seconds//3600}:{(x.seconds//60)%60}:{x.seconds%60}"
        )

        # store sensor metrics in a single source of truth
        self.sensor_metrics["freshness"] = {
            sensor: {
                "float": row["float"] if not pd.isna(row["float"]) else 0,
                "norm": row["norm"] if not pd.isna(row["norm"]) else 0,
                "log": row["log"] if not pd.isna(row["log"]) else 0,
                "norm_log": row["norm_log"] if not pd.isna(row["norm_log"]) else 0,
                "string": row["string"] if not pd.isna(row["string"]) else "No data",
            }
            for sensor, row in df.set_index("sensor_name").iterrows()
        }

        # ensure all active sensors are in the metrics
        for sensor in self.active_sensors:
            if sensor not in self.sensor_metrics["freshness"]:
                self.sensor_metrics["freshness"][sensor] = {
                    "float": 0,
                    "norm": 0,
                    "log": 0,
                    "norm_log": 0,
                    "string": "No data",
                }

        return df

    @handle_data_errors(default_return=lambda: [])
    def get_test_predictions(self):
        file_path = create_file_path(
            get_test_predictions_path, pipeline_output_data_filename
        )
        return self._read_or_compute_data(
            file_path, lambda: [(t[0], t[1], t[2]) for t in self.test_metrics]
        )

    @handle_data_errors(default_return=lambda: [])
    def get_train_metrics(self):
        file_path = create_file_path(
            get_train_metrics_path, pipeline_output_data_filename
        )
        return self._read_or_compute_data(
            file_path, lambda: [(t[0], t[3], t[4]) for t in self.trained_models]
        )

    @handle_data_errors(default_return=lambda: [])
    def get_test_metrics(self):
        file_path = create_file_path(
            get_test_predictions_path, pipeline_output_data_filename
        )
        return self._read_or_compute_data(
            file_path, lambda: [(t[0], t[3]) for t in self.test_metrics]
        )

    @handle_data_errors(default_return=lambda: [])
    def _compute_anomalies(self):
        print(f"Debug: Computing anomalies")
        if not self.test_metrics:
            print(f"Debug: No test metrics found")
            return []

        app_data = []
        for data_tuple in self.test_metrics:
            sensor_name, predictions, labels = data_tuple[0], data_tuple[1].flatten(), data_tuple[2].flatten()
            print(f"Debug: Processing anomalies for sensor {sensor_name}")

            df = pd.DataFrame(
                {
                    "predictions": predictions,
                    "labels": labels,
                }
            )
            df["errors"] = df["predictions"] - df["labels"]
            threshold = df["errors"].mean() + self.anomaly_std * df["errors"].std()
            df["anomaly"] = df["errors"].apply(
                lambda x: True if x > threshold else False
            )
            app_data.append((sensor_name, df))
        print(f"Debug: Computed anomalies for {len(app_data)} sensors")
        return app_data

    @handle_data_errors(default_return=lambda: [])
    def get_anomalies(self):
        file_path = create_file_path(get_anomalies_path, pipeline_output_data_filename)
        print(f"Debug: Getting anomalies from: {file_path}")
        anomalies = self._read_or_compute_data(file_path, self._compute_anomalies)
        # print(f"Debug: Anomalies retrieved: {anomalies[:5] if anomalies else 'None'}")
        return anomalies

    @handle_data_errors(default_return=lambda: [])
    def get_total_anomalies(self, sensor_name):
        anomalies = find_tuple_by_first_element(self.get_anomalies(), sensor_name)
        if anomalies is None:
            return 0
        return anomalies["anomaly"].sum()

    @handle_data_errors(default_return=pd.DataFrame())
    def get_preprocessing_table(self, sensor_name=None):
        sensor_name = sensor_name or self.active_sensors[0]
        data = find_tuple_by_first_element(load_preprocessed_data(), sensor_name)
        return pd.DataFrame() if data is None or data.empty else data.reset_index()

    @handle_data_errors(default_return=pd.DataFrame())
    def get_engineering_table(self, sensor_name=None):
        sensor_name = sensor_name or self.active_sensors[0]
        data = find_tuple_by_first_element(load_engineered_data(), sensor_name)
        return pd.DataFrame() if data is None or data.empty else data.reset_index()

    @handle_data_errors(default_return=lambda: [[], [], [], []])
    def get_training_windows(self):
        file_path = create_file_path(
            get_training_windows_path, pipeline_output_data_filename
        )
        return self._read_or_compute_data(file_path, self._compute_training_windows)

    def _compute_training_windows(self):
        app_data = [[], [], [], []]
        for _, (sensor_name, _, _, val_dataloader, _) in enumerate(self.dataloader):
            unbatched_input_feature, unbatched_labels, unbatched_eng_features = (
                unbatch_dataloaders_to_numpy(val_dataloader)
            )
            app_data[0].append(unbatched_input_feature)
            app_data[1].append(unbatched_labels)
            app_data[2].append(unbatched_eng_features)
            app_data[3].append(sensor_name)
        return app_data

    def get_metadata(self):
        sample_from_test_metrics = []

        for sensor_name, predictions, labels, _ in self.test_metrics:
            sample_from_test_metrics.append((sensor_name, predictions, labels))
            break

        sensor_name = sample_from_test_metrics[0][0]
        sample_predictions = sample_from_test_metrics[0][1]
        sample_labels = sample_from_test_metrics[0][2]

        metadata = {
            "Number of Sensors": len(self.sensors),
            "Number of Active Sensors": len(self.active_sensors),
            "Number of Trainable Sensors": len(self.trainable_sensors),
            "Number of Days": self.n_days,
            "Length of Sensor Info:": len(self.get_sensor_info()[0]),
            "Length of Completeness Metrics": len(self.get_completeness_metrics()),
            "Length of Freshness Metrics": len(self.get_freshness_metrics()),
            "Length of Daily Counts": len(self.get_daily_counts()),
            "Length of Test Metrics": len(self.test_metrics),
            "Length of Sample from Test Metrics": len(sample_from_test_metrics),
            "Sensor Name from Sample": sensor_name,
            "Length of Sample Predictions": len(sample_predictions),
            "Length of Sample Labels": len(sample_labels),
        }
        return metadata

    @handle_data_errors(default_return=lambda: "")
    def get_random_sensor(self):
        random.seed(42)
        return random.choice(self.active_sensors)
