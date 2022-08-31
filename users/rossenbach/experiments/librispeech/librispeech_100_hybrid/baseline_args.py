import copy
import numpy as np
from typing import List

from i6_core.returnn.config import ReturnnConfig
from i6_experiments.common.setups.rasr.util import HybridArgs

from i6_experiments.common.setups.returnn_common.serialization import (
    DataInitArgs,
    DimInitArgs,
    Collection,
    Network,
    ExternData,
    Import
)


from .default_tools import RETURNN_COMMON

def blstm_network(layers, input_layers, dropout=0.1, l2=0.0):
    num_layers = len(layers)
    assert num_layers > 0

    network = {}

    for idx, size in enumerate(layers):
        idx += 1
        for direction, name in [(1, "fwd"), (-1, "bwd")]:
            if idx == 1:
                from_layers = input_layers
            else:
                from_layers = [
                    "lstm_fwd_{}".format(idx - 1),
                    "lstm_bwd_{}".format(idx - 1),
                ]
            network["lstm_{}_{}".format(name, idx)] = {
                "class": "rec",
                "unit": "nativelstm2",
                "direction": direction,
                "n_out": size,
                "dropout": dropout,
                "L2": l2,
                "from": from_layers,
            }

    output_layers = ["lstm_fwd_{}".format(num_layers), "lstm_bwd_{}".format(num_layers)]

    return network, output_layers


def get_nn_args(num_outputs: int = 12001, num_epochs: int = 250):
    evaluation_epochs  = list(np.arange(250, num_epochs + 1, 10))

    returnn_configs = get_returnn_configs(
        num_inputs=50, num_outputs=num_outputs, batch_size=5000,
        evaluation_epochs=evaluation_epochs
    )

    returnn_recog_configs = get_returnn_configs(
        num_inputs=50, num_outputs=num_outputs, batch_size=5000,
        evaluation_epochs=evaluation_epochs,
        recognition=True,
    )


    training_args = {
        "log_verbosity": 5,
        "num_epochs": num_epochs,
        "num_classes": num_outputs,
        "save_interval": 1,
        "keep_epochs": None,
        "time_rqmt": 168,
        "mem_rqmt": 7,
        "cpu_rqmt": 3,
        "partition_epochs": {"train": 40, "dev": 20},
        "use_python_control": False,
    }
    recognition_args = {
        "dev-other": {
            "epochs": evaluation_epochs,
            "feature_flow_key": "gt",
            "prior_scales": [0.3],
            "pronunciation_scales": [6.0],
            "lm_scales": [20.0],
            "lm_lookahead": True,
            "lookahead_options": None,
            "create_lattice": True,
            "eval_single_best": True,
            "eval_best_in_lattice": True,
            "search_parameters": {
                "beam-pruning": 12.0,
                "beam-pruning-limit": 100000,
                "word-end-pruning": 0.5,
                "word-end-pruning-limit": 15000,
            },
            "lattice_to_ctm_kwargs": {
                "fill_empty_segments": True,
                "best_path_algo": "bellman-ford",
            },
            "optimize_am_lm_scale": True,
            "rtf": 50,
            "mem": 8,
            "lmgc_mem": 16,
            "cpu": 4,
            "parallelize_conversion": True,
        },
    }
    test_recognition_args = None

    nn_args = HybridArgs(
        returnn_training_configs=returnn_configs,
        returnn_recognition_configs=returnn_recog_configs,
        training_args=training_args,
        recognition_args=recognition_args,
        test_recognition_args=test_recognition_args,
    )

    return nn_args


def get_feature_extraction_args():
    dc_detection = False
    samples_options = {
        'audio_format': "wav",
        'dc_detection': dc_detection,
    }

    return {
        "gt": {
            "gt_options": {
                "minfreq": 100,
                "maxfreq": 7500,
                "channels": 50,
                # "warp_freqbreak": 7400,
                "tempint_type": "hanning",
                "tempint_shift": 0.01,
                "tempint_length": 0.025,
                "flush_before_gap": True,
                "do_specint": False,
                "specint_type": "hanning",
                "specint_shift": 4,
                "specint_length": 9,
                "normalize": True,
                "preemphasis": True,
                "legacy_scaling": False,
                "without_samples": False,
                "samples_options": samples_options,
                "normalization_options": {},
            }
        }
    }

def get_returnn_configs(
        num_inputs: int, num_outputs: int, batch_size: int, evaluation_epochs: List[int],
        recognition=False,
):
    # ******************** blstm base ********************

    base_config = {
        "extern_data": {
            "data": {"dim": num_inputs},
            "classes": {"dim": num_outputs, "sparse": True},
        },
    }
    base_post_config = {
        "use_tensorflow": True,
        "debug_print_layer_output_template": True,
        "log_batch_size": True,
        "tf_log_memory_usage": True,
        "cache_size": "0",

    }
    if not recognition:
        base_post_config["cleanup_old_models"] = {
            "keep_last_n": 5,
            "keep_best_n": 5,
            "keep": evaluation_epochs,
        }

    network, last_layer = blstm_network([1024]*8, ["specaug"], dropout=0.0, l2=0.0)
    from .specaugment_clean_legacy import specaug_layer, get_funcs

    network["specaug"] = specaug_layer(["data"])
    network["out_linear"] = {
        "class": "linear",
        "activation": None,
        "from": last_layer,
        "n_out": num_outputs,
    }
    network["output"] = {
        "class": "activation",
        "activation": "softmax",
        "from": ["out_linear"],
        "loss": "ce",
        'loss_opts': {'focal_loss_factor': 2.0},
        "target": "classes"
    }
    network["log_output"] = {
        "class": "activation",
        "activation": "log_softmax",
        "from": ["out_linear"],
    }

    if recognition:
        network["log_output"]["is_output_layer"] = True


    blstm_base_config = copy.deepcopy(base_config)
    blstm_base_config.update(
        {
            "batch_size": batch_size,  # {"classes": batch_size, "data": batch_size},
            "chunking": "50:25",
            "optimizer": {"class": "nadam", "epsilon": 1e-8},
            "gradient_noise": 0.3,
            "learning_rates": list(np.linspace(2.5e-5, 2.5e-4, 10)),
            "learning_rate_control": "newbob_multi_epoch",
            "learning_rate_control_min_num_epochs_per_new_lr": 3,
            "learning_rate_control_relative_error_relative_lr": True,
            #"min_learning_rate": 1e-5,
            "newbob_learning_rate_decay": 0.707,
            "newbob_multi_num_epochs": 40,
            "newbob_multi_update_interval": 1,
            "network": network,
        }
    )

    blstm_base_returnn_config = ReturnnConfig(
        config=blstm_base_config,
        post_config=base_post_config,
        hash_full_python_code=True,
        python_prolog=get_funcs(),
        pprint_kwargs={"sort_dicts": False},
    )

    return {
        "blstm_base": blstm_base_returnn_config,
    }


def get_default_data_init_args(num_inputs: int, num_outputs: int):
    """
    default for this hybrid model

    :param num_inputs:
    :param num_outputs:
    :return:
    """
    time_dim = DimInitArgs("data_time", dim=None)
    data_feature = DimInitArgs("data_feature", dim=num_inputs)
    classes_feature = DimInitArgs("classes_feature", dim=num_outputs)

    return [
        DataInitArgs(name="data", available_for_inference=True, dim_tags=[time_dim, data_feature], sparse_dim=None),
        DataInitArgs(name="classes", available_for_inference=False, dim_tags=[time_dim], sparse_dim=classes_feature)
    ]

def get_rc_returnn_configs(
        num_inputs: int, num_outputs: int, batch_size: int, evaluation_epochs: List[int],
        recognition=False,
):
    # ******************** blstm base ********************

    base_config = {
    }
    base_post_config = {
        "use_tensorflow": True,
        "debug_print_layer_output_template": True,
        "log_batch_size": True,
        "tf_log_memory_usage": True,
        "cache_size": "0",

    }

    blstm_base_config = copy.deepcopy(base_config)
    blstm_base_config.update(
        {
            "batch_size": batch_size,  # {"classes": batch_size, "data": batch_size},
            "chunking": "50:25",
            "optimizer": {"class": "nadam", "epsilon": 1e-8},
            "gradient_noise": 0.3,
            "learning_rates": list(np.linspace(2.5e-5, 2.5e-4, 10)),
            "learning_rate_control": "newbob_multi_epoch",
            "learning_rate_control_min_num_epochs_per_new_lr": 3,
            "learning_rate_control_relative_error_relative_lr": True,
            #"min_learning_rate": 1e-5,
            "newbob_learning_rate_decay": 0.707,
            "newbob_multi_num_epochs": 40,
            "newbob_multi_update_interval": 1,
        }
    )
    if not recognition:
        base_post_config["cleanup_old_models"] = {
            "keep_last_n": 5,
            "keep_best_n": 5,
            "keep": evaluation_epochs,
        }

    rc_extern_data = ExternData(extern_data=get_default_data_init_args(num_inputs=num_inputs, num_outputs=num_outputs))

    rc_package = "i6_experiments.users.rossenbach.experiments.librispeech.librispeech_100_hybrid.rc_networks"
    rc_encoder = Import(rc_package + ".default_hybrid.BLSTMEncoder")
    rc_construction_code = Import(rc_package + ".default_hybrid.construct_hybrid_network")

    rc_network = Network(
        net_func_name=rc_construction_code.object_name,
        net_func_map={
            "encoder": rc_encoder.object_name,
            "train": not recognition,
            "audio_data": "data",
            "label_data": "classes"
        },
        net_kwargs={
            "num_layers": 6,
            "size": 512,
            "dropout": 0.1,
        },
    )

    serializer = Collection(
        serializer_objects=[
            rc_extern_data,
            rc_encoder,
            rc_construction_code,
            rc_network,
        ],
        returnn_common_root=RETURNN_COMMON,
        make_local_package_copy=True,
        packages={
            rc_package,
        },
    )

    blstm_base_returnn_config = ReturnnConfig(
        config=blstm_base_config,
        post_config=base_post_config,
        python_epilog=[serializer],
        pprint_kwargs={"sort_dicts": False},
    )

    return {
        "blstm_base": blstm_base_returnn_config,
    }
