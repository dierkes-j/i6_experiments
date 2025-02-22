from .base import BaseTdpModel, Arch, TdpModelBuilder

TDP_FFNN_LAYER = lambda num_classes: {
    "class": "subnetwork",
    "from": ["fwd_6", "bwd_6"],
    "subnetwork": {
        "fwd_prob": {
            "class": "linear",
            "activation": "log_sigmoid",
            "n_out": num_classes,
        },
        "loop_prob": {
            "class": "eval",
            "eval": "safe_log(1 - tf.exp(source(0)))",
            "from": ["fwd_prob"],
        },
        "output": {
            "class": "stack",
            "from": ["fwd_prob", "loop_prob"],
            "axis": -1,
        }
    }
}

BLSTM_CONFIG = {
    "dropout": 0.1,
    "L2": 0.0001
}

TDP_BLSTM_NO_LABEL_LAYER = lambda num_classes: {
    "class": "subnetwork",
    "from": ["fwd_6", "bwd_6"],
    "subnetwork": {
        "lstm_fwd": {
            'class'     : 'rec',
            'unit'      : 'lstmp',
            'direction' : 1,
            'n_out'     : 1,
            **BLSTM_CONFIG,
        },
        "lstm_bwd": {
            'class'     : 'rec',
            'unit'      : 'lstmp',
            'direction' : -1,
            'n_out'     : 1,
            **BLSTM_CONFIG,
        },
        "fwd_prob": {
            "activation": "log_sigmoid",
            "class": "linear",
            "from": ["lstm_fwd", "lstm_bwd"],
            "n_out": 1,
        },
        "loop_prob": {
            "class": "eval",
            "eval": "safe_log(1 - tf.exp(source(0)))",
            "from": ["fwd_prob"],
        },
        "output": {
            "class": "expand_dims",
            "from": ["fwd_prob", "loop_prob"],
            "axis": "spatial",
            "dim": num_classes,
        },
    }
}


TDP_SIGMOID_NO_LABEL = lambda num_classes: {
    "for_all_labels": {
        "class": "tile",
        "multiples": {"F": num_classes},
        "from": ["fwd_prob"],
    },
    "loop_prob": {
        "class": "eval",
        "eval": "safe_log(1 - tf.exp(source(0)))",
        "from": ["for_all_labels"],
    },
    "output": {
        "class": "stack",
        "from": ["for_all_labels", "loop_prob"],
        "axis": -1,
    }
}

TDP_SIGMOID = {
    "loop_prob": {
        "class": "eval",
        "eval": "safe_log(1 - tf.exp(source(0)))",
        "from": ["fwd_prob"],
    },
    "output": {
        "class": "stack",
        "from": ["fwd_prob", "loop_prob"],
        "axis": -1,
    }
}

TPD_BLSTM_NO_LABEL_SIGMOID_LAYER = lambda num_classes: {
    "class": "subnetwork",
    "from": ["fwd_6", "bwd_6"],
    "subnetwork": {
        "lstm_fwd": {
            'class'     : 'rec',
            'unit'      : 'lstmp',
            'direction' : 1,
            'n_out'     : 1,
            **BLSTM_CONFIG,
        },
        "lstm_bwd": {
            'class'     : 'rec',
            'unit'      : 'lstmp',
            'direction' : -1,
            'n_out'     : 1,
            **BLSTM_CONFIG,
        },
        "fwd_prob": {
            "class": "linear",
            "activation": "log_sigmoid",
            "from": ["lstm_fwd", "lstm_bwd"],
            "n_out": 1,
        },
        **TDP_SIGMOID_NO_LABEL(num_classes),
    }
}

TPD_BLSTM_LAYER_SIGMOID = lambda num_classes: {
    "class": "subnetwork",
    "from": ["fwd_6", "bwd_6"],
    "subnetwork": {
        "lstm_fwd": {
            'class'     : 'rec',
            'unit'      : 'lstmp',
            'direction' : 1,
            'n_out'     : 1,
            **BLSTM_CONFIG,
        },
        "lstm_bwd": {
            'class'     : 'rec',
            'unit'      : 'lstmp',
            'direction' : -1,
            'n_out'     : 1,
            **BLSTM_CONFIG,
        },
        "fwd_prob": {
            "class": "linear",
            "activation": "log_sigmoid",
            "from": ["lstm_fwd", "lstm_bwd"],
            "n_out": num_classes,
        },
        **TDP_SIGMOID.copy(),
    }
}

FEATURE_ARCHS = {
    'blstm_no_label': TDP_BLSTM_NO_LABEL_LAYER,
    "ffnn": TDP_FFNN_LAYER,
    "blstm_no_label_sigmoid": TPD_BLSTM_NO_LABEL_SIGMOID_LAYER,
    "blstm": TPD_BLSTM_LAYER_SIGMOID,
}

def get_tdp_layer(num_classes, arch: Arch="ffnn"):
    return FEATURE_ARCHS[arch](num_classes)


TDP_OUTPUT_LAYER = {
    "class": "eval",
    "eval": "-source(0)",
    "from": "tdps",
    "loss": "via_layer",
    "loss_opts": {'error_signal_layer': 'fast_bw/tdps'}
}

TDP_OUTPUT_LAYER_AS_FUNC = lambda num_classes: TDP_OUTPUT_LAYER

TDP_OUTPUT_LAYER_W_SOFTMAX = {
    "class": "copy",
    "from": "tdps",
    "loss": "via_layer",
    "loss_opts": {'align_layer': 'fast_bw/tdps', 'loss_wrt_to_act_in': 'log_softmax'}
}

FEATURE_MODEL_BUILDERS = {
    # 'blstm_no_label': TdpModelBuilder(TDP_BLSTM_NO_LABEL_LAYER, TDP_OUTPUT_LAYER_AS_FUNC),
    'ffnn': TdpModelBuilder(TDP_FFNN_LAYER, TDP_OUTPUT_LAYER_AS_FUNC),
    'blstm_no_label_sigmoid': TdpModelBuilder(TPD_BLSTM_NO_LABEL_SIGMOID_LAYER, TDP_OUTPUT_LAYER_AS_FUNC),
    'blstm': TdpModelBuilder(TPD_BLSTM_LAYER_SIGMOID, TDP_OUTPUT_LAYER_AS_FUNC),
    'blstm_no_label': TdpModelBuilder(TDP_BLSTM_NO_LABEL_LAYER, TDP_OUTPUT_LAYER_AS_FUNC)
}

SOFTMAX_ARCHS = [
    'blstm_no_label',
]

def get_model(num_classes, arch):
    return BaseTdpModel(
        tdp_layer=get_tdp_layer(num_classes, arch),
        output_layer=TDP_OUTPUT_LAYER_W_SOFTMAX if arch in SOFTMAX_ARCHS else TDP_OUTPUT_LAYER,
    )
