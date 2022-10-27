import copy

import numpy

from sisyphus import tk

from i6_core.tools import CloneGitRepositoryJob
from i6_core.returnn import ReturnnConfig

from .pipeline import \
    build_training_datasets, build_test_dataset, training, search, get_best_checkpoint, search_single

from .attention_asr_config import create_config, ConformerEncoderArgs, TransformerDecoderArgs, RNNDecoderArgs
from .zeineldeen_helpers.models.lm.transformer_lm import TransformerLM

from .feature_extraction_net import log10_net_10ms

def conformer_tf_features():
    returnn_exe = tk.Path("/u/rossenbach/bin/returnn/returnn_tf2.3.4_mkl_launcher.sh", hash_overwrite="GENERIC_RETURNN_LAUNCHER")
    returnn_root_datasets = CloneGitRepositoryJob("https://github.com/rwth-i6/returnn",
                                                  commit="c2b31983bfc1049c7ce1549d7ffed153c9d4a443").out_repository
    prefix_name = "experiments/librispeech/librispeech_960_attention/conformer_2022"

    # build the training datasets object containing train, cv, dev-train and the extern_data dict
    training_datasets_speedperturbed = build_training_datasets(
        returnn_exe,
        returnn_root_datasets,
        prefix_name,
        bpe_size=10000,
        use_raw_features=True,
        link_speed_perturbation=True
    )

    # build testing datasets
    test_dataset_tuples = {}
    for testset in ["dev-clean", "dev-other", "test-clean", "test-other"]:
        test_dataset_tuples[testset] = build_test_dataset(testset, returnn_python_exe=returnn_exe, returnn_root=returnn_root_datasets, output_path=prefix_name, use_raw_features=True)

    # ------------------------------------------------------------------------------------------------------------------- #

    conformer_enc_args = ConformerEncoderArgs(
        num_blocks=12, input_layer='lstm-6', att_num_heads=8, ff_dim=2048, enc_key_dim=512, conv_kernel_size=32,
        pos_enc='rel', dropout=0.1, att_dropout=0.1, l2=0.0001)

    # fairseq init
    fairseq_ff_init = "variance_scaling_initializer(mode='fan_avg', distribution='uniform', scale=1.0)"  # limit = sqrt(6 / (fan_in + fan_out))
    fairseq_mhsa_init = "variance_scaling_initializer(mode='fan_avg', distribution='uniform', scale=0.5)"  # limit = sqrt(6 * 0.5 / (fan_in + fan_out)) = sqrt(3 / (fan_in + fan_out))
    conformer_enc_args.ff_init = fairseq_ff_init
    conformer_enc_args.mhsa_init = fairseq_mhsa_init
    conformer_enc_args.mhsa_out_init = fairseq_ff_init
    conformer_enc_args.conv_module_init = fairseq_ff_init
    conformer_enc_args.ctc_loss_scale = 1.0

    rnn_dec_args = RNNDecoderArgs()

    training_args = {}

    # LR scheduling
    training_args['const_lr'] = [42, 100]  # use const LR during pretraining
    training_args['wup_start_lr'] = 0.0002
    training_args['wup'] = 20

    training_args['speed_pert'] = True

    # overwrite BN params
    conformer_enc_args.batch_norm_opts = {
        'momentum': 0.1,
        'epsilon': 1e-3,
        'update_sample_only_in_training': False,
        'delay_sample_update': False
    }

    # pretraining
    training_args['pretrain_opts'] = {'variant': 4, "initial_batch_size": 20000*200}
    training_args['pretrain_reps'] = 6

    # ---------------------------------------------------------
    # LM Settings
    # transf_lm_net = TransformerLM(
    #     source='prev:output', num_layers=24, vocab_size=2051, use_as_ext_lm=True, prefix_name='lm_')
    # transf_lm_net.create_network()
    # transf_lm_opts = {
    #     'lm_subnet': transf_lm_net.network.get_net(),
    #     'lm_output_prob_name': 'lm_output',
    #     'is_recurrent': True,
    #     'preload_from_files': {
    #         'lm_model': {
    #             'filename': '/work/asr4/zeineldeen/setups-data/librispeech/2021-02-21--lm-bpe/dependencies/lm_models/transf/epoch.016',
    #             'prefix': 'lm_'
    #         }
    #     },
    #     'name': 'trafo',
    # }
    # ---------------------------------------------------------

    # conformer round 2
    name = 'tf_feature_conformer_12l_lstm_1l_normal_v2'
    local_conformer_enc_args = copy.deepcopy(conformer_enc_args)
    local_conformer_enc_args.ctc_loss_scale = 1.0
    local_training_args = copy.deepcopy(training_args)

    # pretraining
    local_training_args['pretrain_opts'] = {'variant': 3, "initial_batch_size": 18000*200}
    local_training_args['pretrain_reps'] = 5
    local_training_args['batch_size'] = 12000*200  # frames * samples per frame

    exp_prefix = prefix_name + "/" + name
    args = copy.deepcopy({**local_training_args, "encoder_args": local_conformer_enc_args, "decoder_args": rnn_dec_args})
    args['name'] = name
    args['with_staged_network'] = True
    returnn_root = CloneGitRepositoryJob("https://github.com/rwth-i6/returnn",
                                         commit="3f62155a08722310f51276792819b3c7c64ad356").out_repository

    def run_exp_v2(ft_name, feature_extraction_net):
        returnn_config = create_config(training_datasets=training_datasets_speedperturbed, **args, feature_extraction_net=feature_extraction_net)
        train_job = training(ft_name, returnn_config, returnn_exe, returnn_root, num_epochs=250)
        search(ft_name + "/default_last", returnn_config, train_job.out_checkpoints[250], test_dataset_tuples, returnn_exe, returnn_root)

        # ext_lm_search_args = copy.deepcopy(args)
        # ext_lm_search_args["ext_lm_opts"] = transf_lm_opts

        # for lm_scale in [0.36, 0.38, 0.4, 0.42, 0.44]:
        #     search_args = copy.deepcopy(ext_lm_search_args)
        #     search_args['ext_lm_opts']['lm_scale'] = lm_scale
        #     returnn_config = create_config(training_datasets=training_datasets, **search_args, feature_extraction_net=feature_extraction_net)
        #     returnn_config.config["batch_size"] = 10000*200  # smaller size for recognition
        #     search_single(ft_name + "/default_last_ext_lm_%.2f" % lm_scale,
        #                   returnn_config,
        #                   train_job.out_checkpoints[250],
        #                   test_dataset_tuples["dev-other"][0],
        #                   test_dataset_tuples["dev-other"][1],
        #                   returnn_exe,
        #                   returnn_root)

    run_exp_v2(exp_prefix + "/" + "raw_log10", log10_net_10ms)

