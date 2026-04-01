from backend.ml.trainer import train_dummy_model


if __name__ == "__main__":
    metrics = train_dummy_model()
    print("Training complete:", metrics)
